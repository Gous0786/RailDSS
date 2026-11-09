"""DS-1 ingestion and data quality (spec FR-ING-01..07, §6.2, DQ-01..03; issue #70).

Pipeline for every record of an ``obs.*`` / ``ops.*`` topic:

1. **Validate** against the envelope schema; invalid records go to the topic's dead-letter topic with
   the reason (FR-ING-01). A valid envelope of a type its source adapter does not carry is rejected too.
2. **De-duplicate** on ``event_id`` (FR-ING-02, DQ-02).
3. **Order** on event time with a watermark per source: an event is released once the source's
   watermark (newest event time or heartbeat of the source minus ``ingest.late_threshold_min``) has
   passed it, in ``(event_time, train_run_id, seq)`` order. An event already behind the watermark when
   it arrives is **late** (FR-ING-03): it is released at once with ``quality.late = true`` and goes into
   history, but must not drive the current plan. :meth:`Ingestor.ordered` is the history in
   ``(train_run_id, source, seq)`` order and :meth:`Ingestor.state_hash` its hash, so a degraded feed
   (duplicates, out of order) and a clean one give the same hash once the watermark has passed.
4. **Feed health** (FR-ING-04): a P4 "data stale" alert when a source has sent no heartbeat for
   ``ingest.heartbeat_missed_limit`` heartbeat periods; a recovery record when heartbeats return.
5. **Stale trains** (FR-ING-05): the age of each train's last position or station event; stale above
   ``state.stale_threshold_min``.
6. **Per-source metrics** (FR-ING-06): received, duplicates, out of order, late, rejected, latency
   P50 / P95 and, for RTIS, completeness.

Sources sit behind :class:`SourceAdapter`, named after the IR system each feed mimics (FR-ING-07). The
transport is separate (:class:`MemoryTransport`, :class:`JsonLinesTransport` for twin trace files, or
Kafka through :class:`raildss_common.bus.EventConsumer` with :attr:`Ingestor.processor`), so a real
adapter can replace a simulated one without core changes.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import heapq
import json
import statistics
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from raildss_common.bus import EventProcessor, Outcome, Rejection
from raildss_common.envelope import EventEnvelope, EventType
from raildss_params import Register, load

SECONDS_PER_MINUTE = 60
PERCENTILES = 20  # statistics.quantiles cut points: 5 % steps, so P50 and P95 are exact cut points
P50, P95 = 9, 18  # indices of the 50th and 95th percentile among the 19 cut points


# --------------------------------------------------------------------------- source adapters (FR-ING-07)
@dataclass(frozen=True)
class SourceAdapter:
    """One external source: the IR system it mimics, its topic and the event types it carries."""

    system: str
    topic: str
    event_types: frozenset[EventType]

    def accepts(self, event: EventEnvelope) -> bool:
        return event.event_type in self.event_types


ADAPTERS: tuple[SourceAdapter, ...] = (
    SourceAdapter("RTIS", "obs.position", frozenset({EventType.TRAIN_POSITION})),
    SourceAdapter("COA", "obs.station", frozenset({EventType.STATION_EVENT})),
    SourceAdapter("LOGGER_EI", "obs.occupancy", frozenset({EventType.TDS_OCCUPANCY})),
    SourceAdapter(
        "BLOCK_INSTRUMENTS",
        "obs.block",
        frozenset({EventType.LINE_CLEAR, EventType.TRAIN_OUT_OF_SECTION}),
    ),
    SourceAdapter("FEED_HEALTH", "obs.heartbeat", frozenset({EventType.HEARTBEAT})),
    SourceAdapter("CAUTION_ORDER", "ops.tsr", frozenset({EventType.TSR_ISSUED, EventType.TSR_WITHDRAWN})),
    SourceAdapter("BLOCK_PROGRAMME", "ops.blocks", frozenset({EventType.MAINT_BLOCK})),
    SourceAdapter(
        "CONTROL_LOG",
        "ops.disruptions",
        frozenset({EventType.DISRUPTION_RAISED, EventType.DISRUPTION_UPDATED, EventType.DISRUPTION_CLEARED}),
    ),
    SourceAdapter("FOIS", "ops.rakes", frozenset({EventType.RAKE_READY})),
    SourceAdapter("CMS", "ops.crew", frozenset({EventType.CREW_STATUS})),
    SourceAdapter("IMD", "ops.weather", frozenset({EventType.WEATHER})),
)
BY_TOPIC = {a.topic: a for a in ADAPTERS}
FIX_TYPES = frozenset({EventType.TRAIN_POSITION, EventType.STATION_EVENT})
RTIS_TOPIC = "obs.position"
HEARTBEAT_TOPIC = "obs.heartbeat"


# --------------------------------------------------------------------------- transports
class MemoryTransport:
    """Records held in memory: ``(topic, raw bytes)`` pairs, in delivery order."""

    def __init__(self, records: Iterable[tuple[str, bytes]]) -> None:
        self._records = list(records)

    def records(self) -> Iterator[tuple[str, bytes]]:
        yield from self._records


class JsonLinesTransport:
    """A twin trace file (``python -m raildss_twin run --trace-out``): one ``{"topic", "event"}`` per line.

    Only the topics of a source adapter are read; ground truth (``gt.*``) is skipped, never ingested.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def records(self) -> Iterator[tuple[str, bytes]]:
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                record = json.loads(line)
                if record["topic"] in BY_TOPIC:
                    yield record["topic"], json.dumps(record["event"]).encode("utf-8")


# --------------------------------------------------------------------------- results
@dataclass(frozen=True)
class DqAlert:
    """A data-quality alert (FR-ING-04). ``kind`` DATA_STALE raises it, SOURCE_RECOVERED clears it."""

    source: str
    kind: Literal["DATA_STALE", "SOURCE_RECOVERED"]
    priority: str
    time: dt.datetime
    last_heartbeat: dt.datetime | None
    detail: str


@dataclass(frozen=True)
class TrainFix:
    """Age of a train's last position or station event (FR-ING-05)."""

    train_run_id: str
    last_fix: dt.datetime
    age_s: float
    stale: bool


@dataclass
class SourceMetrics:
    """Per-source data-quality counters (FR-ING-06)."""

    received: int = 0
    delivered: int = 0
    duplicates: int = 0
    rejected: int = 0
    out_of_order: int = 0
    late: int = 0
    latencies_s: list[float] = field(default_factory=list)
    fixes: dict[str, list[dt.datetime]] = field(default_factory=dict)  # RTIS: fix times per train

    def summary(self, rtis_interval_s: float | None = None) -> dict[str, Any]:
        lat = sorted(self.latencies_s)
        cuts = statistics.quantiles(lat, n=PERCENTILES) if len(lat) > 1 else lat * (PERCENTILES - 1)
        out: dict[str, Any] = {
            "received": self.received,
            "delivered": self.delivered,
            "duplicates": self.duplicates,
            "rejected": self.rejected,
            "out_of_order": self.out_of_order,
            "late": self.late,
            "duplicate_share": self.duplicates / self.received if self.received else 0.0,
            "out_of_order_share": self.out_of_order / self.delivered if self.delivered else 0.0,
            "latency_p50_s": cuts[P50] if cuts else None,
            "latency_p95_s": cuts[P95] if cuts else None,
        }
        if rtis_interval_s and self.fixes:
            expected = sum(
                int((last - first).total_seconds() // rtis_interval_s) + 1 for first, last, _ in self._spans()
            )
            got = sum(n for _, _, n in self._spans())
            out["completeness"] = got / expected if expected else None
        return out

    def _spans(self) -> Iterator[tuple[dt.datetime, dt.datetime, int]]:
        for times in self.fixes.values():
            yield min(times), max(times), len(times)


# --------------------------------------------------------------------------- the ingestor
class _Processor(EventProcessor):
    """The bus processor, aware of the record's topic (adapter checks and per-source duplicate counts)."""

    def __init__(self, ingestor: Ingestor) -> None:
        super().__init__(ingestor._accept, on_reject=ingestor._reject)
        self.ingestor = ingestor

    def process(
        self,
        raw: bytes,
        *,
        topic: str | None = None,
        partition: int | None = None,
        offset: int | None = None,
        key: bytes | None = None,
    ) -> Outcome:
        self.ingestor._topic = topic
        outcome = super().process(raw, topic=topic, partition=partition, offset=offset, key=key)
        if outcome == "duplicate":
            self.ingestor._metrics_for(topic).duplicates += 1
            self.ingestor._metrics_for(topic).received += 1
        return outcome


class Ingestor:
    """DS-1: validated, de-duplicated, event-time ordered feeds with data-quality monitoring.

    ``on_event`` receives released events (late ones flagged); ``on_alert`` receives :class:`DqAlert`;
    ``dead_letter`` receives each :class:`Rejection` (route it to the topic's DLQ). Feed records with
    :meth:`ingest`, advance the clock with :meth:`tick` and call :meth:`flush` at the end of a replay.
    """

    def __init__(
        self,
        register: Register | None = None,
        *,
        on_event: Callable[[EventEnvelope], None] | None = None,
        on_alert: Callable[[DqAlert], None] | None = None,
        dead_letter: Callable[[Rejection], None] | None = None,
    ) -> None:
        reg = register or load()
        self.lateness = dt.timedelta(minutes=float(reg.value("ingest.late_threshold_min")))
        self.stale_s = float(reg.value("state.stale_threshold_min")) * SECONDS_PER_MINUTE
        self.heartbeat_s = float(reg.value("obs.heartbeat_period_s"))
        self.silence_s = self.heartbeat_s * float(reg.value("ingest.heartbeat_missed_limit"))
        self.rtis_interval_s = float(reg.value("rtis.position_interval_s"))
        self.on_event, self.on_alert, self.dead_letter = on_event, on_alert, dead_letter
        self.processor = _Processor(self)
        self.metrics: dict[str, SourceMetrics] = {}
        self.history: list[EventEnvelope] = []
        self.released: list[EventEnvelope] = []
        self.alerts: list[DqAlert] = []
        self.rejections: list[Rejection] = []
        self.now: dt.datetime | None = None
        self._topic: str | None = None
        self._order = 0
        self._buffer: dict[str, list[tuple[dt.datetime, str, int, int, EventEnvelope]]] = {}
        self._newest: dict[str, dt.datetime] = {}
        self._watermark: dict[str, dt.datetime] = {}
        self._top_seq: dict[tuple[str, str | None], int] = {}
        self._last_heartbeat: dict[str, dt.datetime] = {}
        self._stale_sources: set[str] = set()
        self._last_fix: dict[str, dt.datetime] = {}

    # ------------------------------------------------------------------ input
    def ingest(self, topic: str, raw: bytes) -> Outcome:
        """Process one record of ``topic`` (any transport)."""
        return self.processor.process(raw, topic=topic)

    def run(self, transport: MemoryTransport | JsonLinesTransport) -> None:
        """Replay a transport to the end: the clock follows delivery times and ticks every heartbeat
        period in between, so a silent source is detected while it is silent."""
        for topic, raw in transport.records():
            try:
                when = _delivered_at(raw)
            except ValueError:
                when = None
            if when is not None:
                self._tick_until(when)
            self.ingest(topic, raw)
        self.flush()

    def tick(self, now: dt.datetime) -> None:
        """Advance the ingestion clock and check feed health (FR-ING-04)."""
        if self.now is None or now > self.now:
            self.now = now
        limit = dt.timedelta(seconds=self.silence_s)
        for source, last in self._last_heartbeat.items():
            if source not in self._stale_sources and self.now - last > limit:
                self._stale_sources.add(source)
                self._alert(
                    DqAlert(
                        source,
                        "DATA_STALE",
                        "P4",
                        self.now,
                        last,
                        f"no heartbeat for {(self.now - last).total_seconds():.0f} s "
                        f"(limit {self.silence_s:.0f} s)",
                    )
                )

    def flush(self) -> None:
        """Release every buffered event (end of a replay or shutdown)."""
        for source in list(self._buffer):
            self._release(source, None)

    # ------------------------------------------------------------------ outputs
    def ordered(self) -> list[EventEnvelope]:
        """History in ``(train_run_id, source, seq)`` order (FR-ING-02)."""
        return sorted(self.history, key=lambda e: (e.train_run_id or "", e.source, e.seq))

    def state_hash(self) -> str:
        """Hash of the ordered history, ignoring delivery facts (``ingest_time``, ``quality``)."""
        h = hashlib.sha256()
        for e in self.ordered():
            h.update(e.model_dump_json(exclude={"ingest_time", "quality"}).encode("utf-8") + b"\n")
        return h.hexdigest()

    def train_fixes(self, now: dt.datetime | None = None) -> list[TrainFix]:
        """Age of every train's last fix and its stale flag (FR-ING-05)."""
        at = now or self.now
        if at is None:
            return []
        out = []
        for train, last in sorted(self._last_fix.items()):
            age = (at - last).total_seconds()
            out.append(TrainFix(train, last, age, age > self.stale_s))
        return out

    @property
    def stale_sources(self) -> set[str]:
        """Sources currently in a DATA_STALE alert."""
        return set(self._stale_sources)

    def summary(self) -> dict[str, dict[str, Any]]:
        """Per-source data-quality metrics (FR-ING-06)."""
        return {
            s: m.summary(self.rtis_interval_s if s == "RTIS" else None)
            for s, m in sorted(self.metrics.items())
        }

    # ------------------------------------------------------------------ internals
    def _metrics_for(self, topic: str | None) -> SourceMetrics:
        adapter = BY_TOPIC.get(topic or "")
        name = adapter.system if adapter else str(topic)
        return self.metrics.setdefault(name, SourceMetrics())

    def _reject(self, rejection: Rejection) -> None:
        m = self._metrics_for(rejection.topic)
        m.received += 1
        m.rejected += 1
        self.rejections.append(rejection)
        if self.dead_letter is not None:
            self.dead_letter(rejection)

    def _accept(self, event: EventEnvelope) -> None:
        topic = self._topic
        adapter = BY_TOPIC.get(topic or "")
        if adapter is None or not adapter.accepts(event):
            why = f"{event.event_type} is not carried by topic {topic}"
            self.processor.stats.rejected += 1
            self._reject(Rejection(why, (why,), event.to_json(), topic))
            return
        m = self._metrics_for(topic)
        m.received += 1
        m.delivered += 1
        arrived = event.ingest_time or event.event_time
        m.latencies_s.append((arrived - event.event_time).total_seconds())
        self.tick(arrived)
        if event.event_type == EventType.HEARTBEAT:
            self._heartbeat(event)
            return
        key = (event.source, event.train_run_id)
        if event.seq < self._top_seq.get(key, -1):
            m.out_of_order += 1
        self._top_seq[key] = max(event.seq, self._top_seq.get(key, -1))
        if event.train_run_id is not None and event.event_type in FIX_TYPES:
            last = self._last_fix.get(event.train_run_id)
            self._last_fix[event.train_run_id] = (
                event.event_time if last is None else max(last, event.event_time)
            )
            if topic == RTIS_TOPIC:
                m.fixes.setdefault(event.train_run_id, []).append(event.event_time)
        watermark = self._watermark.get(event.source)
        if watermark is not None and event.event_time < watermark:
            m.late += 1
            late = event.model_copy(update={"quality": event.quality.model_copy(update={"late": True})})
            self.history.append(late)
            self._emit(late)
            return
        self.history.append(event)
        self._order += 1
        heapq.heappush(
            self._buffer.setdefault(event.source, []),
            (event.event_time, event.train_run_id or "", event.seq, self._order, event),
        )
        newest = self._newest.get(event.source)
        if newest is None or event.event_time > newest:
            self._newest[event.source] = event.event_time
            self._advance(event.source, event.event_time - self.lateness)

    def _heartbeat(self, event: EventEnvelope) -> None:
        source = str(event.payload["source"])
        self._last_heartbeat[source] = max(
            self._last_heartbeat.get(source, event.event_time), event.event_time
        )
        if source in self._stale_sources:
            self._stale_sources.discard(source)
            self._alert(
                DqAlert(
                    source, "SOURCE_RECOVERED", "P4", event.event_time, event.event_time, "heartbeats resumed"
                )
            )
        self._advance(source, event.event_time - self.lateness)

    def _advance(self, source: str, watermark: dt.datetime) -> None:
        current = self._watermark.get(source)
        if current is None or watermark > current:
            self._watermark[source] = watermark
            self._release(source, watermark)

    def _release(self, source: str, watermark: dt.datetime | None) -> None:
        heap = self._buffer.get(source, [])
        while heap and (watermark is None or heap[0][0] <= watermark):
            self._emit(heapq.heappop(heap)[-1])

    def _emit(self, event: EventEnvelope) -> None:
        self.released.append(event)
        if self.on_event is not None:
            self.on_event(event)

    def _alert(self, alert: DqAlert) -> None:
        self.alerts.append(alert)
        if self.on_alert is not None:
            self.on_alert(alert)

    def _tick_until(self, when: dt.datetime) -> None:
        if self.now is None:
            self.tick(when)
            return
        step = dt.timedelta(seconds=self.heartbeat_s)
        t = self.now + step
        while t < when:
            self.tick(t)
            t += step
        self.tick(when)


def _delivered_at(raw: bytes) -> dt.datetime | None:
    """Delivery time of a raw record (``ingest_time``, else ``event_time``), or None if unreadable."""
    try:
        data = json.loads(raw)
        text = data.get("ingest_time") or data.get("event_time")
        return dt.datetime.fromisoformat(text) if isinstance(text, str) else None
    except (json.JSONDecodeError, AttributeError, TypeError) as exc:
        raise ValueError(str(exc)) from None
