"""Observation model and degradation profiles (spec FR-TWIN-13, §6.4, §7.1, REC-27; issue #52).

The twin's ground truth (``gt.*``) is for evaluation only (FR-TWIN-14). The DSS sees the railway
through the simulated external feeds of §7.1, which an :class:`Observer` derives from that truth and
degrades per a profile D0-D3 (§6.4, register key ``obs.degradation_profile``):

=================  ===============  ===========================================================
Topic              Source           Derived from
=================  ===============  ===========================================================
``obs.position``   ``SIM.RTIS``     the train's position every ``rtis.position_interval_s`` while
                                    it is in the section (a standing loco reports speed 0)
``obs.station``    ``SIM.COA``      route set to receive (``ARR``) or send (``DEP``) at a station
``obs.block``      ``SIM.BLOCK``    an absolute-block line taken (``LINE_CLEAR_GRANTED``) and freed
                                    (``TRAIN_OUT_OF_SECTION``)
``obs.occupancy``  ``SIM.LOGGER``   station lines and automatic sub-blocks occupied and cleared
``obs.heartbeat``  ``SIM.HEALTH``   per feed, every ``obs.heartbeat_period_s``
=================  ===============  ===========================================================

Degradation (all draws are seeded, so the same seed gives the same feeds):

* **RTIS**: coverage per train, random dropout, optional Gaussian km noise. Fixes taken during an
  outage are lost.
* **COA station events**: reported at the station-event resolution (minute in D1). They arrive after a
  latency drawn once per station visit, and never before the train's previous entry. Mid-path
  departures go missing. Some entries are sent twice, with the same ``event_id``, and some arrive
  after the train's next entry (out of order).
* **Logger**: only a covered share of track sections reports (partial occupancy).
* **Outages**: no positions and no heartbeats; station, block and logger events are held and
  delivered when the outage ends (store and forward).

Every observed event is an :class:`EventEnvelope` whose ``event_time`` is what the feed reports and
whose ``ingest_time`` is when the feed delivered it. Downstream sinks receive the events in delivery
order. Like the real log, the feeds carry no trustworthy derived fields (§6.4): consumers recompute
distance and speed from reference data.

The observer only reads the simulation, so attaching one leaves the ground-truth trace hash unchanged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import heapq
import math
import random
import statistics
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from raildss_common.envelope import EventEnvelope, EventType, Quality
from raildss_params import Register
from raildss_twin.plans import SECONDS_PER_DAY
from raildss_twin.sim import M_PER_KM, SECONDS_PER_HOUR, SECTION, Proc, Sim, Sink, Train, token

SECONDS_PER_MINUTE = 60
PROFILES = ("D0", "D1", "D2", "D3")

RTIS, COA, BLOCK, LOGGER, HEALTH = "SIM.RTIS", "SIM.COA", "SIM.BLOCK", "SIM.LOGGER", "SIM.HEALTH"
FEEDS = (RTIS, COA, BLOCK, LOGGER)
TOPIC = {
    RTIS: "obs.position",
    COA: "obs.station",
    BLOCK: "obs.block",
    LOGGER: "obs.occupancy",
    HEALTH: "obs.heartbeat",
}
ACTIVE_STATES = ("running", "waiting", "dwelling")


# --------------------------------------------------------------------------- profiles
@dataclass(frozen=True)
class Latency:
    """Delivery latency in seconds: half the draws below the median, half above, each uniform."""

    low_s: float = 0.0
    p50_s: float = 0.0
    high_s: float = 0.0

    @property
    def mean_s(self) -> float:
        return statistics.fmean((self.low_s, self.p50_s, self.p50_s, self.high_s))

    def sample(self, rng: random.Random) -> float:
        if self.high_s <= 0:
            return 0.0
        return rng.uniform(*rng.choice(((self.low_s, self.p50_s), (self.p50_s, self.high_s))))


@dataclass(frozen=True)
class Profile:
    """One degradation profile of spec §6.4. Rates are fractions; times are seconds."""

    name: str
    rtis_coverage: float = 1.0
    position_dropout: float = 0.0
    position_noise_m: float = 0.0
    station_resolution_s: float = 1.0
    latency: Latency = Latency()
    missing_departure: float = 0.0
    duplicates: float = 0.0
    out_of_order: float = 0.0
    logger_coverage: float = 1.0
    outage_every_s: float | None = None  # one outage in every period of this length (D2)
    outage_s: tuple[float, float] = (0.0, 0.0)  # outage duration bounds; one per day when no period


def load_profile(register: Register, name: str) -> Profile:
    """Profile ``name`` (D0-D3) from the parameter register."""
    key = name.upper()
    if key not in PROFILES:
        raise ValueError(f"unknown degradation profile {name!r} (expected one of {', '.join(PROFILES)})")
    if key == "D0":
        return Profile("D0")
    minute = float(SECONDS_PER_MINUTE)
    lat = register.value("obs.station_event_latency_min")
    d1 = Profile(
        "D1",
        rtis_coverage=float(register.value("obs.rtis_coverage")),
        position_dropout=float(register.value("obs.position_dropout_rate")),
        position_noise_m=float(register.value("obs.position_noise_m")),
        station_resolution_s=float(register.value("obs.station_event_resolution_s")),
        latency=Latency(float(lat["min"]) * minute, float(lat["p50"]) * minute, float(lat["max"]) * minute),
        missing_departure=float(register.value("obs.missing_departure_rate")),
        duplicates=float(register.value("obs.duplicate_rate")),
        out_of_order=float(register.value("obs.out_of_order_rate")),
        logger_coverage=float(register.value("obs.logger_coverage")),
    )
    if key == "D1":
        return d1
    cfg: Mapping[str, Any] = register.value("obs.degradation_profile")
    if key == "D2":
        d2 = cfg["d2"]
        low, high = float(d2["latency_min"]["low"]) * minute, float(d2["latency_min"]["high"]) * minute
        outage = d2["outage"]
        duration = float(outage["duration_min"]) * minute
        return dataclasses.replace(
            d1,
            name="D2",
            rtis_coverage=float(d2["rtis_coverage"]),
            position_dropout=float(d2["position_dropout"]),
            latency=Latency(low, statistics.fmean((low, high)), high),
            missing_departure=float(d2["missing_departure"]),
            duplicates=float(d2["duplicates"]),
            out_of_order=float(d2["out_of_order"]),
            logger_coverage=float(d2["logger_coverage"]),
            outage_every_s=float(outage["every_h"]) * SECONDS_PER_HOUR,
            outage_s=(duration, duration),
        )
    d3 = cfg["d3"]
    if str(d3["base"]).upper() != "D1":
        raise ValueError(f"obs.degradation_profile d3 must build on d1, not {d3['base']!r}")
    bounds = d3["outage_min"]
    return dataclasses.replace(
        d1, name="D3", outage_s=(float(bounds["low"]) * minute, float(bounds["high"]) * minute)
    )


# --------------------------------------------------------------------------- observer
class Observer:
    """Publishes the ``obs.*`` feeds of one simulation run under one degradation profile.

    Use through :func:`raildss_twin.run.run_day` (``observers=[...]``), or by hand: pass
    :meth:`on_ground_truth` to the simulation as (part of) its sink, call :meth:`attach` before
    :meth:`Sim.run` and :meth:`close` after it. ``keep=True`` keeps the delivered events in
    :attr:`events`, in delivery order.
    """

    def __init__(
        self, register: Register, profile: Profile | str, *, seed: int | None = None, keep: bool = False
    ) -> None:
        self.profile = profile if isinstance(profile, Profile) else load_profile(register, profile)
        self.interval_s = float(register.value("rtis.position_interval_s"))
        self.heartbeat_s = float(register.value("obs.heartbeat_period_s"))
        self.keep = keep
        self.events: list[tuple[str, EventEnvelope]] = []
        self.counts: dict[str, int] = {}
        self.outages: list[tuple[float, float]] = []
        self._seed_override = seed
        self.seed = 0
        self._sim: Sim | None = None
        self._downstream: Sink | None = None
        self._queue: list[tuple[float, int, str, EventEnvelope]] = []
        self._order = 0
        self._seq: dict[str, int] = {}
        self._rng: dict[str, random.Random] = {}
        self._covered: dict[str, bool] = {}
        self._first_station: dict[str, str] = {}
        self._last_station: dict[str, str] = {}
        self._visit_latency: dict[tuple[str, str], float] = {}
        self._last_coa: dict[str, float] = {}
        self._held: dict[str, EventEnvelope] = {}
        self._window: dict[str, list[float]] = {f: [] for f in FEEDS}

    # ------------------------------------------------------------------ lifecycle
    @property
    def sim(self) -> Sim:
        if self._sim is None:
            raise RuntimeError("observer is not attached to a simulation")
        return self._sim

    def attach(self, sim: Sim, downstream: Sink | None = None) -> None:
        """Start observing ``sim``; delivered events go to ``downstream`` (e.g. the Kafka producer)."""
        if self._sim is not None:
            raise RuntimeError("observer is already attached")
        self._sim, self._downstream = sim, downstream
        self.seed = sim.seed if self._seed_override is None else self._seed_override
        self._rng = {
            purpose: random.Random(f"raildss-obs/{self.seed}/{self.profile.name}/{purpose}")
            for purpose in ("outage", "dropout", "noise", "latency", "missing", "duplicate", "order")
        }
        for t in sim.trains:
            stations = [n.op for n in t.plan.nodes if not sim.layout.ops[n.op].is_halt]
            if stations:
                self._last_station[token(t.plan.run_id)] = stations[-1]
        self.outages = self._plan_outages(sim.until_s)
        sim.env.process(self._sample_positions())
        sim.env.process(self._heartbeats())

    def close(self) -> None:
        """Deliver everything still queued or held back (call after the run)."""
        for event in self._held.values():
            self._release(event, self.sim.env.now)
        self._held.clear()
        self._deliver(math.inf)

    def on_ground_truth(self, topic: str, event: EventEnvelope) -> None:
        """Sink for the simulation's ``gt.*`` events."""
        now = self.sim.env.now
        self._deliver(now)
        if topic == "gt.signal" and event.event_type == EventType.ROUTE_SET:
            self._station_event(event, now)
        elif topic == "gt.occupancy":
            label = str(event.payload["tds_id"])
            occupied = bool(event.payload["occupied"])
            if label.startswith("BL:"):
                self._block_event(event, label, occupied, now)
            elif self._logged(label):
                self._logger_event(label, occupied, now)
        self._deliver(now)

    # ------------------------------------------------------------------ outages
    def _plan_outages(self, horizon_s: float) -> list[tuple[float, float]]:
        low, high = self.profile.outage_s
        if high <= 0:
            return []
        rng = self._rng["outage"]
        every = self.profile.outage_every_s

        def window(offset: float, span: float) -> tuple[float, float]:
            # whole seconds, so an event forwarded at the end is exactly representable as an envelope time
            duration = float(round(rng.uniform(low, high)))
            start = float(round(offset + rng.uniform(0.0, max(0.0, span - duration))))
            return start, start + duration

        if every is None:
            return [window(0.0, SECONDS_PER_DAY)]
        return [window(k * every, every) for k in range(math.ceil(horizon_s / every))]

    def in_outage(self, s: float) -> bool:
        return any(a <= s < b for a, b in self.outages)

    def _after_outage(self, s: float) -> float:
        for a, b in self.outages:
            if a <= s < b:
                return b
        return s

    # ------------------------------------------------------------------ envelopes and delivery
    def _make(
        self,
        source: str,
        etype: EventType,
        *,
        event_s: float,
        ingest_s: float,
        payload: dict[str, Any],
        train_run_id: str | None = None,
        location: Mapping[str, Any] | None = None,
        noisy: bool = False,
    ) -> EventEnvelope:
        sim = self.sim
        seq = self._seq[source] = self._seq.get(source, 0) + 1
        name = f"raildss-obs/{self.seed}/{sim.scenario_id}/{self.profile.name}/{source}/{seq}"
        return EventEnvelope(
            event_id=uuid.uuid5(uuid.NAMESPACE_URL, name),
            event_type=etype,
            schema_version="1.0",
            source=source,
            section_id=SECTION,
            network_version=sim.layout.network_version,
            scenario_id=sim.scenario_id,
            seq=seq,
            event_time=sim.origin + dt.timedelta(seconds=event_s),
            ingest_time=sim.origin + dt.timedelta(seconds=ingest_s),
            train_run_id=train_run_id,
            location=location,  # type: ignore[arg-type]
            payload=payload,
            quality=Quality(simulated_noise=noisy),
        )

    def _push(self, ingest_s: float, topic: str, event: EventEnvelope) -> None:
        self._order += 1
        heapq.heappush(self._queue, (ingest_s, self._order, topic, event))

    def _deliver(self, until_s: float) -> None:
        sim = self.sim
        while self._queue and self._queue[0][0] <= until_s:
            ingest_s, _, topic, event = heapq.heappop(self._queue)
            self.counts[topic] = self.counts.get(topic, 0) + 1
            if event.source in self._window:
                lag = ingest_s - (event.event_time - sim.origin).total_seconds()
                self._window[event.source].append(lag)
            if self.keep:
                self.events.append((topic, event))
            if self._downstream is not None:
                self._downstream(topic, event)

    def _ingest_of(self, event: EventEnvelope) -> float:
        assert event.ingest_time is not None
        return (event.ingest_time - self.sim.origin).total_seconds()

    def _with_ingest(self, event: EventEnvelope, ingest_s: float) -> EventEnvelope:
        return event.model_copy(update={"ingest_time": self.sim.origin + dt.timedelta(seconds=ingest_s)})

    def _release(self, held: EventEnvelope, not_before_s: float) -> None:
        """Queue a held-back station event, delivered no earlier than ``not_before_s`` and never during an
        outage (it is then forwarded when the outage ends)."""
        at = self._after_outage(max(not_before_s, self._ingest_of(held)))
        self._push(at, TOPIC[COA], self._with_ingest(held, at))

    # ------------------------------------------------------------------ RTIS positions
    def covered(self, run_id: str) -> bool:
        """Does this train's loco report RTIS positions (drawn once per train, independent of order)?"""
        hit = self._covered.get(run_id)
        if hit is None:
            draw = random.Random(f"raildss-obs/{self.seed}/{self.profile.name}/rtis/{run_id}").random()
            hit = self._covered[run_id] = draw < self.profile.rtis_coverage
        return hit

    def _sample_positions(self) -> Proc:
        sim = self.sim
        while True:
            now = sim.env.now
            self._deliver(now)
            for t in sim.trains:
                if t.entered and t.status in ACTIVE_STATES:
                    self._position(t, now)
            self._deliver(now)
            yield sim.env.timeout(self.interval_s)

    def _position(self, t: Train, now: float) -> None:
        run_id = token(t.plan.run_id)
        if not self.covered(run_id) or self.in_outage(now):
            return
        p = self.profile
        if p.position_dropout > 0 and self._rng["dropout"].random() < p.position_dropout:
            return
        km, speed = self.sim.position(t)
        location: dict[str, Any] = {"km": km}
        if p.position_noise_m > 0:
            km += self._rng["noise"].gauss(0.0, p.position_noise_m) / M_PER_KM
            location = {"km": km, "accuracy_m": p.position_noise_m}
        event = self._make(
            RTIS,
            EventType.TRAIN_POSITION,
            event_s=now,
            ingest_s=now,
            payload={"speed_kmh": speed},
            train_run_id=run_id,
            location=location,
            noisy=p.position_noise_m > 0,
        )
        self._push(now, TOPIC[RTIS], event)

    # ------------------------------------------------------------------ COA station events
    def _station_event(self, gt: EventEnvelope, now: float) -> None:
        p = self.profile
        run_id = gt.train_run_id
        assert run_id is not None
        station = str(gt.payload["station"])
        kind = "ARR" if gt.payload["mode"] == "receive" else "DEP"
        first = self._first_station.setdefault(run_id, station)
        mid_path_departure = kind == "DEP" and station != first
        if (
            mid_path_departure
            and p.missing_departure > 0
            and self._rng["missing"].random() < p.missing_departure
        ):
            return
        visit = (run_id, station)
        latency = self._visit_latency.get(visit)
        if latency is None:
            latency = self._visit_latency[visit] = p.latency.sample(self._rng["latency"])
        ingest = self._after_outage(max(now + latency, self._last_coa.get(run_id, 0.0)))
        self._last_coa[run_id] = ingest
        exact = dt.timedelta(seconds=now).total_seconds()  # microseconds, as the envelope times are
        reported = math.floor(exact / p.station_resolution_s) * p.station_resolution_s
        event = self._make(
            COA,
            EventType.STATION_EVENT,
            event_s=reported,
            ingest_s=ingest,
            payload={
                "station": station,
                "kind": kind,
                "line_used": str(gt.payload["line"]),
                "manual": True,
                "reported_time": (self.sim.origin + dt.timedelta(seconds=reported)).isoformat(),
            },
            train_run_id=run_id,
            location=gt.location.model_dump(exclude_none=True) if gt.location else None,
        )
        last = kind == "ARR" and self._last_station.get(run_id) == station
        late = (
            not last and p.out_of_order > 0 and self._rng["order"].random() < p.out_of_order
        )  # sent after the train's next entry
        previous = self._held.pop(run_id, None)
        if late:
            if previous is not None:
                self._release(previous, now)
            self._held[run_id] = event
        else:
            self._push(ingest, TOPIC[COA], event)
            if previous is not None:
                self._release(previous, ingest)
        if p.duplicates > 0 and self._rng["duplicate"].random() < p.duplicates:
            copy_s = self._after_outage(ingest + p.latency.sample(self._rng["duplicate"]))
            self._push(copy_s, TOPIC[COA], self._with_ingest(event, copy_s))

    # ------------------------------------------------------------------ block instruments and logger
    def _block_event(self, gt: EventEnvelope, label: str, occupied: bool, now: float) -> None:
        segment, _, line = label.removeprefix("BL:").partition("/")
        payload: dict[str, Any] = {
            "block_section": segment.replace("~", "-"),
            "line": line,
            "kind": "LINE_CLEAR_GRANTED" if occupied else "TRAIN_OUT_OF_SECTION",
            "train_run_id": gt.train_run_id,
        }
        etype = EventType.LINE_CLEAR if occupied else EventType.TRAIN_OUT_OF_SECTION
        ingest = self._after_outage(now)
        event = self._make(
            BLOCK, etype, event_s=now, ingest_s=ingest, payload=payload, train_run_id=gt.train_run_id
        )
        self._push(ingest, TOPIC[BLOCK], event)

    def _logged(self, label: str) -> bool:
        cover = self.profile.logger_coverage
        if cover >= 1:
            return True
        draw = random.Random(f"raildss-obs/{self.seed}/{self.profile.name}/logger/{label}").random()
        return draw < cover

    def _logger_event(self, label: str, occupied: bool, now: float) -> None:
        ingest = self._after_outage(now)
        event = self._make(
            LOGGER,
            EventType.TDS_OCCUPANCY,
            event_s=now,
            ingest_s=ingest,
            payload={"tds_id": label, "occupied": occupied},
        )  # a track circuit does not know which train it detects
        self._push(ingest, TOPIC[LOGGER], event)

    # ------------------------------------------------------------------ heartbeats
    def _heartbeats(self) -> Proc:
        sim = self.sim
        while True:
            yield sim.env.timeout(self.heartbeat_s)
            now = sim.env.now
            self._deliver(now)
            if not self.in_outage(now):
                for source in FEEDS:
                    lags = self._window[source]
                    payload = {
                        "source": source,
                        "lag_s": round(statistics.fmean(lags), 1) if lags else 0.0,
                        "count_10s": len(lags),
                    }
                    event = self._make(
                        HEALTH, EventType.HEARTBEAT, event_s=now, ingest_s=now, payload=payload
                    )
                    self._push(now, TOPIC[HEALTH], event)
                self._deliver(now)
            for lags in self._window.values():
                lags.clear()
