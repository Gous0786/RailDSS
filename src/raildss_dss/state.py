"""DS-3 state estimation and snapshots (spec FR-PRED-01, FR-PRED-02; issue #71).

:class:`StateEstimator` fuses the released ``obs.*`` events of DS-1 (:mod:`raildss_dss.ingest`) into one
state per train:

* **resource**: the station (``ARR``, until the train leaves) or the block it runs in. A departure at
  station X puts the train in the block from X to the next block station of its planned path; a train
  with no station event yet (one resumed inside a block) is placed from its RTIS km on that path. A
  missing departure is recovered when a later fix shows the train moving away from X;
* **km and speed**: the last RTIS fix, never extrapolated (FR-PRED-01). After
  ``state.stale_threshold_min`` without a position or station event the train is **stale** and its
  position is reported as unknown;
* **delay**: of the last station event against the booked time (passenger trains);
* the train leaves the state when it has arrived at the end of its path and its fixes have stopped.

:meth:`StateEstimator.snapshot` returns the snapshot ``S_t``: the train states, the ``network_version``,
a SHA-256 over both and the time, and a version number that increases on every material change (train
set, resources, stale flags) (FR-PRED-02). The same events give the same hashes.

Plans come from the published network (:class:`PlanDirectory`): the DSS reads reference data and
the timetable, never the twin.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from raildss_common.envelope import EventEnvelope, EventType
from raildss_params import Register, load

SECONDS_PER_MINUTE = 60


def token(run_id: str) -> str:
    """Run ids on the bus contain no spaces (envelope ``Token``)."""
    return run_id.replace(" ", "_")


# --------------------------------------------------------------------------- reference data and plans
@dataclass
class PlanDirectory:
    """Station chainage, directional block ids and each train's planned path from the published network."""

    network_version: str
    km: dict[str, float]
    halts: set[str]
    blocks: dict[tuple[str, str], str]  # (from station, to station) -> block id
    paths: dict[str, list[str]] = field(default_factory=dict)  # run token -> block stations in order
    booked: dict[tuple[str, str, str], dt.datetime] = field(default_factory=dict)  # (run, station, ARR|DEP)

    @classmethod
    def from_published(
        cls, network_version: str | None = None, register: Register | None = None
    ) -> PlanDirectory:
        from raildss_etl import publish
        from raildss_etl.timetable import ist

        reg = register or load()
        version = network_version or str(reg.value("network.version"))
        _, tables, _ = publish.read_export(publish.default_dir(version))
        ops = tables["operational_point"]
        d = cls(
            network_version=version,
            km={o.code: float(o.km) for o in ops},  # type: ignore[attr-defined]
            halts={o.code for o in ops if o.is_halt},  # type: ignore[attr-defined]
            blocks={(b.from_op, b.to_op): b.id for b in tables["block_section"]},  # type: ignore[attr-defined]
        )
        for ld in tables[
            "freight_load"
        ]:  # entry to exit along the section (the log's row order is not a route)
            ends = [ld.entry_point, ld.exit_point]  # type: ignore[attr-defined]
            d.paths[token(ld.load_id)] = d._block_stations(ends)  # type: ignore[attr-defined]
        d._passenger(tables, ist())
        return d

    def _block_stations(self, stations: list[str]) -> list[str]:
        """Block stations of a path, with the ones a non-adjacent log block (e.g. VZM-KTV) passes inserted
        by chainage (the section is linear)."""
        known = [s for s in stations if s in self.km and s not in self.halts]
        blocks = sorted((c for c in self.km if c not in self.halts), key=lambda c: self.km[c])
        out: list[str] = []
        for s in known:
            if out and out[-1] != s:
                a, b = self.km[out[-1]], self.km[s]
                between = [c for c in blocks if min(a, b) < self.km[c] < max(a, b)]
                out.extend(between if b > a else reversed(between))
            if not out or out[-1] != s:
                out.append(s)
        return out

    def _passenger(self, tables: dict[str, Any], tz: dt.tzinfo) -> None:
        self._points: dict[str, list[Any]] = defaultdict(list)
        for p in tables["timetable_point"]:
            self._points[str(p.train_id)].append(p)
        self._train_id = {str(s.train_no): str(s.train_id) for s in tables["service"]}
        self._tz = tz

    def path(self, run_id: str) -> list[str] | None:
        """Block stations of the train's planned path (passenger runs are built on first use)."""
        if run_id in self.paths:
            return self.paths[run_id]
        if "@" not in run_id or not hasattr(self, "_points"):
            return None
        train_no, _, date = run_id.partition("@")
        points = sorted(self._points.get(self._train_id.get(train_no, ""), []), key=lambda p: p.seq)
        if not points:
            return None
        midnight = dt.datetime.combine(dt.date.fromisoformat(date), dt.time(0), tzinfo=self._tz)
        for p in points:
            self.booked[(run_id, p.op_code, "ARR")] = midnight + dt.timedelta(seconds=float(p.arr_s))
            self.booked[(run_id, p.op_code, "DEP")] = midnight + dt.timedelta(seconds=float(p.dep_s))
        self.paths[run_id] = self._block_stations([p.op_code for p in points])
        return self.paths[run_id]

    def next_block(self, run_id: str, station: str) -> str | None:
        """Block from ``station`` to the next block station of the train's path."""
        path = self.path(run_id) or []
        if station in path:
            i = path.index(station)
            if i + 1 < len(path):
                return self.blocks.get((station, path[i + 1]))
        return None

    @property
    def section_ends(self) -> set[str]:
        """The first and last block stations by chainage: a train arriving there leaves the section."""
        stations = [c for c in self.km if c not in self.halts]
        return (
            {min(stations, key=self.km.__getitem__), max(stations, key=self.km.__getitem__)}
            if stations
            else set()
        )

    def next_station(self, run_id: str, station: str) -> str | None:
        path = self.path(run_id) or []
        i = path.index(station) if station in path else -1
        return path[i + 1] if 0 <= i < len(path) - 1 else None

    def block_at(self, run_id: str, km: float) -> str | None:
        """Block of the train's path whose station chainages contain ``km``."""
        path = self.path(run_id) or []
        for a, b in itertools.pairwise(path):
            lo, hi = sorted((self.km[a], self.km[b]))
            if lo <= km <= hi:
                return self.blocks.get((a, b))
        return None


# --------------------------------------------------------------------------- state
@dataclass
class _Train:
    run_id: str
    station: str | None = None  # station of the last station event
    kind: str | None = None  # ARR | DEP
    line: str | None = None
    station_time: dt.datetime | None = None
    km: float | None = None
    speed_kmh: float | None = None
    fix_time: dt.datetime | None = None  # last position fix
    last_event: dt.datetime | None = None  # last position or station event
    delay_s: float | None = None


@dataclass(frozen=True)
class TrainState:
    run_id: str
    resource: str | None  # block id or station code; None if unknown
    station_line: str | None
    km: float | None  # None when the position is unknown (stale)
    speed_kmh: float | None
    delay_s: float | None
    last_fix_age_s: float | None
    stale: bool

    @property
    def position_known(self) -> bool:
        return not self.stale and self.km is not None


@dataclass(frozen=True)
class Snapshot:
    """``S_t`` (spec §6.1 ``StateSnapshot``)."""

    t: dt.datetime
    network_version: str
    version: int
    trains: tuple[TrainState, ...]
    hash: str

    def train(self, run_id: str) -> TrainState | None:
        return next((s for s in self.trains if s.run_id == run_id), None)

    def to_json(self) -> dict[str, Any]:
        return {
            "t": self.t.isoformat(),
            "network_version": self.network_version,
            "version": self.version,
            "hash": self.hash,
            "trains": [s.__dict__ for s in self.trains],
        }


class StateEstimator:
    """DS-3. Feed released events with :meth:`apply` (in event-time order) and read :meth:`snapshot`."""

    def __init__(self, directory: PlanDirectory, register: Register | None = None) -> None:
        reg = register or load()
        self.directory = directory
        self.stale_s = float(reg.value("state.stale_threshold_min")) * SECONDS_PER_MINUTE
        self.fix_interval_s = float(reg.value("rtis.position_interval_s"))
        self.trains: dict[str, _Train] = {}
        self.version = 0
        self._material: tuple[Any, ...] | None = None

    def apply(self, event: EventEnvelope) -> None:
        """Update the state with one event. Late events (``quality.late``) belong to history only."""
        if event.quality.late or event.train_run_id is None:
            return
        if event.event_type == EventType.TRAIN_POSITION and event.location and event.location.km is not None:
            tr = self.trains.setdefault(event.train_run_id, _Train(event.train_run_id))
            if tr.fix_time is None or event.event_time >= tr.fix_time:
                tr.km, tr.fix_time = float(event.location.km), event.event_time
                tr.speed_kmh = float(event.payload["speed_kmh"])
            tr.last_event = max(tr.last_event or event.event_time, event.event_time)
        elif event.event_type == EventType.STATION_EVENT:
            tr = self.trains.setdefault(event.train_run_id, _Train(event.train_run_id))
            if tr.station_time is None or event.event_time >= tr.station_time:
                tr.station, tr.kind = str(event.payload["station"]), str(event.payload["kind"])
                tr.station_time = event.event_time
                if tr.kind == "ARR":
                    tr.line = event.payload.get("line_used")
                booked = self.directory.booked.get((tr.run_id, tr.station, tr.kind))
                if booked is None:
                    self.directory.path(tr.run_id)
                    booked = self.directory.booked.get((tr.run_id, tr.station, tr.kind))
                if booked is not None:
                    tr.delay_s = (event.event_time - booked).total_seconds()
            tr.last_event = max(tr.last_event or event.event_time, event.event_time)

    def resource(self, tr: _Train) -> tuple[str | None, str | None]:
        """(resource, station line) of a train from its last station event and fix."""
        d = self.directory
        if tr.station is not None and tr.kind == "ARR":
            moved = (
                tr.fix_time is not None
                and tr.station_time is not None
                and tr.fix_time > tr.station_time
                and (tr.speed_kmh or 0) > 0
                and tr.km is not None
                and tr.km != d.km.get(tr.station)
            )
            if not moved:
                return tr.station, tr.line
        if tr.station is not None:
            block = d.next_block(tr.run_id, tr.station)
            if block is not None:
                ahead = d.next_station(tr.run_id, tr.station)
                if ahead is not None and tr.km is not None and tr.km == d.km.get(ahead):
                    return ahead, None  # held at the next station's limits, waiting to be received
                return block, None
        if tr.km is not None:
            return d.block_at(tr.run_id, tr.km), None
        return None, None

    def _left(self, tr: _Train, t: dt.datetime) -> bool:
        """Arrived at the end of its path or of the section, and no fix since: the train has left."""
        path = self.directory.path(tr.run_id) or []
        end = bool(path) and tr.station == path[-1]
        if tr.kind != "ARR" or not (end or tr.station in self.directory.section_ends):
            return False
        last = tr.fix_time or tr.station_time
        return last is not None and (t - last).total_seconds() >= self.fix_interval_s

    def snapshot(self, t: dt.datetime) -> Snapshot:
        """``S_t`` from the events applied so far."""
        states = []
        for run_id in sorted(self.trains):
            tr = self.trains[run_id]
            if self._left(tr, t):
                continue
            resource, line = self.resource(tr)
            age = (t - tr.last_event).total_seconds() if tr.last_event else None
            stale = age is None or age > self.stale_s
            states.append(
                TrainState(
                    run_id=run_id,
                    resource=resource,
                    station_line=line,
                    km=None if stale else tr.km,
                    speed_kmh=None if stale else tr.speed_kmh,
                    delay_s=tr.delay_s,
                    last_fix_age_s=age,
                    stale=stale,
                )
            )
        material = tuple((s.run_id, s.resource, s.stale) for s in states)
        if material != self._material:
            self._material = material
            self.version += 1
        body = {
            "t": t.isoformat(),
            "network_version": self.directory.network_version,
            "trains": [s.__dict__ for s in states],
        }
        digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
        return Snapshot(t, self.directory.network_version, self.version, tuple(states), digest)
