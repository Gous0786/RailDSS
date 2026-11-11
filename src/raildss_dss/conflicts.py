"""DS-5 conflict detection (spec FR-CD-01..07, §8.3; OD-25; issue #73).

**Occupations** (FR-CD-01). From the ETA/ETD of DS-4 (:mod:`raildss_dss.predict`) each train occupies, over
the horizon ``cd.horizon_min``, the block segments it runs (from departure to arrival, plus the line-clear
operation time ``block.line_clear_op_time_s`` on absolute block) and the station it stands at (arrival to
departure). Both P50 and P90 intervals are kept.

**Detection** (FR-CD-02), a deterministic sweep per resource:

1. *head-on*: trains in opposite directions on a segment whose usable lines cannot keep them apart
   (single-line working: a line closed by a maintenance window, or a single reversible line);
2. *following / headway*: same-direction trains beyond the segment's line capacity (absolute block: one train
   per line), or entering an automatic-block segment closer than the line-clear time;
3. *crossing infeasibility*: more trains standing at a station than it has usable lines (of sufficient CSR
   when the train length is known);
4. *platform*: more stopping passenger trains than platform lines, or two on the same booked platform;
6. *deadlock risk*: a cycle in the wait-for graph (each train needs the resource the next one holds) at a
   conflict instant;
7. *rules*: a train through a block closed for maintenance on all its usable lines, or a crew whose duty
   (``crew.duty_limit_h``) expires before the train reaches its end of run.

Type 5 (missed overtake) is P2.

**Near-conflicts** (FR-CD-03): a conflict found only on P90 intervals is tagged ``near`` and is P3, unless its
lead time is under ``cd.urgent_lead_min``. **No feasible resolution** (FR-CD-06): a head-on or deadlock whose
trains are all already committed to the segment is listed as ``NO_FEASIBLE_RESOLUTION`` with a P2 alert.
:class:`DetectionSchedule` runs detection on every state event and plan change, and at least every
``cd.rerun_max_interval_s`` (FR-CD-04). :class:`StabilityTracker` reports alert stability (FR-CD-07).
"""

from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from raildss_dss.predict import EventPrediction
from raildss_dss.state import PlanDirectory, Snapshot, StateEstimator
from raildss_params import Register, load

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
EPSILON = dt.timedelta(seconds=1)
HEAD_ON, FOLLOWING, CROSSING, PLATFORM, DEADLOCK, RULE = 1, 2, 3, 4, 6, 7
TYPE_NAMES = {
    HEAD_ON: "head-on (single line)",
    FOLLOWING: "following / headway",
    CROSSING: "crossing infeasibility",
    PLATFORM: "platform",
    DEADLOCK: "deadlock risk",
    RULE: "rule (maintenance block or crew duty)",
}
BASE_PRIORITY = {HEAD_ON: "P1", DEADLOCK: "P1", FOLLOWING: "P2", CROSSING: "P2", PLATFORM: "P2", RULE: "P2"}


Key = tuple[int, tuple[str, ...], str]


def segment(a: str, b: str) -> str:
    """Physical segment of a block section (both directions share its lines)."""
    return "~".join(sorted((a, b)))


# --------------------------------------------------------------------------- resource model
@dataclass(frozen=True)
class Line:
    line_id: str
    direction: str  # DN | UP | BOTH
    absolute: bool  # absolute block (one train per line) vs automatic (following trains allowed)


@dataclass(frozen=True)
class StationLine:
    line_no: int
    platform: bool
    passenger_ok: bool
    csr_m: float | None


@dataclass
class ResourceModel:
    """Segment lines and station lines of the published network."""

    lines: dict[str, list[Line]]  # segment -> lines
    stations: dict[str, list[StationLine]]  # station -> usable main / loop lines

    @classmethod
    def from_published(
        cls, network_version: str | None = None, register: Register | None = None
    ) -> ResourceModel:
        from raildss_etl import publish

        reg = register or load()
        _, tables, _ = publish.read_export(
            publish.default_dir(network_version or str(reg.value("network.version")))
        )
        ends = {b.id: (b.from_op, b.to_op) for b in tables["block_section"]}  # type: ignore[attr-defined]
        lines: dict[str, dict[str, Line]] = defaultdict(dict)
        for r in tables["block_section_line"]:
            a, b = ends[r.block_section_id]  # type: ignore[attr-defined]
            absolute = str(r.system).upper().startswith("ABS")  # type: ignore[attr-defined]
            lines[segment(a, b)][r.line_id] = Line(r.line_id, str(r.direction), absolute)  # type: ignore[attr-defined]
        stations: dict[str, list[StationLine]] = defaultdict(list)
        for s in tables["station_line"]:
            if s.kind in ("main", "loop") and s.status == "in_use":  # type: ignore[attr-defined]
                stations[s.op_code].append(  # type: ignore[attr-defined]
                    StationLine(int(s.line_no), bool(s.platform), bool(s.passenger_ok), s.csr_m)  # type: ignore[attr-defined]
                )
        return cls({k: list(v.values()) for k, v in lines.items()}, dict(stations))


# --------------------------------------------------------------------------- occupations
@dataclass(frozen=True)
class Occupation:
    """A train on a resource: a segment (``kind`` = "block") or a station. Times in P50 and P90."""

    run_id: str
    resource: str
    kind: str  # block | station
    start: dt.datetime
    end: dt.datetime
    start90: dt.datetime
    end90: dt.datetime
    direction: str = ""  # DN | UP for blocks
    stop: bool = False  # standing at the station (not a pass)
    passenger: bool = False
    platform: str | None = None  # booked platform line, if any
    length_m: float | None = None
    committed: bool = False  # already on the resource at detection time
    seq: int = 0  # position in the train's sequence of resources


@dataclass(frozen=True)
class MaintenanceWindow:
    """A segment line (or the whole segment, ``line`` None) closed between ``start`` and ``end``."""

    segment: str
    line: str | None
    start: dt.datetime
    end: dt.datetime


@dataclass(frozen=True)
class CrewDuty:
    run_id: str
    sign_on: dt.datetime
    limit: dt.timedelta


def occupations(
    predictions: Sequence[EventPrediction],
    snapshot: Snapshot,
    estimator: StateEstimator,
    directory: PlanDirectory,
    horizon: dt.timedelta,
) -> list[Occupation]:
    """Resource occupations of every train from its current resource and its predicted events.

    An occupation with no predicted end (its end lies beyond the horizon) runs to the horizon.
    """
    t = snapshot.t
    by_run: dict[str, list[EventPrediction]] = defaultdict(list)
    for e in predictions:
        by_run[e.run_id].append(e)
    out: list[Occupation] = []
    km = directory.km
    for st in snapshot.trains:
        run_id = st.run_id
        events = sorted(by_run.get(run_id, []), key=lambda e: (e.eta_p50, e.kind == "DEP"))
        tr = estimator.trains[run_id]
        passenger = "@" in run_id
        items: list[tuple[str, str, EventPrediction | None, EventPrediction | None, dict[str, Any]]] = []
        if tr.station is not None and tr.kind == "ARR":  # the resource it holds now
            dep = next((e for e in events if e.station == tr.station and e.kind == "DEP"), None)
            items.append(("station", tr.station, None, dep, {"stop": True, "committed": True}))
        elif st.resource is not None and st.resource not in km:
            ends = next((k for k, v in directory.blocks.items() if v == st.resource), None)
            if ends is not None:
                a, b = ends
                arr = next((e for e in events if e.station == b and e.kind == "ARR"), None)
                way = "DN" if km[b] > km[a] else "UP"
                items.append(("block", segment(a, b), None, arr, {"direction": way, "committed": True}))
        for x, y in itertools.pairwise(events):  # what it will hold
            if x.kind == "DEP" and y.kind == "ARR":
                way = "DN" if km[y.station] > km[x.station] else "UP"
                items.append(("block", segment(x.station, y.station), x, y, {"direction": way}))
            elif x.kind == "ARR" and y.kind == "DEP" and x.station == y.station:
                items.append(("station", x.station, x, y, {"stop": y.eta_p50 > x.eta_p50}))
        if events and events[-1].kind == "ARR":
            items.append(("station", events[-1].station, events[-1], None, {"stop": True}))
        far = t + horizon
        for seq, (kind, resource, a_ev, b_ev, kw) in enumerate(items):
            s50, s90 = (a_ev.eta_p50, a_ev.eta_p90) if a_ev else (t, t)
            e50, e90 = (b_ev.eta_p50, b_ev.eta_p90) if b_ev else (far, far)
            if kind == "station":
                kw["passenger"] = passenger
            out.append(Occupation(run_id, resource, kind, s50, e50, s90, e90, seq=seq, **kw))
    return out


# --------------------------------------------------------------------------- conflicts
@dataclass
class Conflict:
    type: int
    trains: tuple[str, ...]
    resource: str
    time: dt.datetime  # predicted start of the conflict
    detected: dt.datetime
    near: bool = False  # only under P90 (FR-CD-03)
    status: str = "OPEN"  # OPEN | NO_FEASIBLE_RESOLUTION
    priority: str = "P2"
    impact_s: float = 0.0  # estimated weighted delay
    detail: str = ""
    recommendation: str | None = None  # linked recommendation id (DS-9)

    @property
    def lead_time_s(self) -> float:
        return (self.time - self.detected).total_seconds()

    @property
    def key(self) -> Key:
        return self.type, self.trains, self.resource


Span = Callable[[Occupation], tuple[dt.datetime, dt.datetime]]
Report = Callable[[Conflict], None]


@dataclass
class Detector:
    resources: ResourceModel
    directory: PlanDirectory
    register: Register
    clearance_s: float = 0.0
    urgent_s: float = 0.0
    weights: dict[str, float] = field(default_factory=dict)
    duty: float = 0.0

    @classmethod
    def build(
        cls,
        directory: PlanDirectory,
        register: Register | None = None,
        resources: ResourceModel | None = None,
    ) -> Detector:
        reg = register or load()
        return cls(
            resources=resources or ResourceModel.from_published(directory.network_version, reg),
            directory=directory,
            register=reg,
            clearance_s=float(reg.value("block.line_clear_op_time_s")),
            urgent_s=float(reg.value("cd.urgent_lead_min")) * SECONDS_PER_MINUTE,
            weights={k: float(v) for k, v in reg.value("policy.weights").items()},
        )

    # ------------------------------------------------------------------ public
    def detect(
        self,
        occs: Sequence[Occupation],
        now: dt.datetime,
        maintenance: Sequence[MaintenanceWindow] = (),
        crew: Sequence[CrewDuty] = (),
    ) -> list[Conflict]:
        """All conflicts: P50 sweep, then the P90 near-conflict band (FR-CD-02/03)."""
        p50 = self._sweep(occs, now, maintenance, crew, p90=False)
        found = {c.key for c in p50}
        near = [c for c in self._sweep(occs, now, maintenance, crew, p90=True) if c.key not in found]
        for c in near:
            c.near = True
        out = p50 + near
        for c in out:
            c.priority = self._priority(c)
        return sorted(out, key=lambda c: (c.time, c.type, c.trains))

    # ------------------------------------------------------------------ the sweep
    def _sweep(
        self,
        occs: Sequence[Occupation],
        now: dt.datetime,
        maintenance: Sequence[MaintenanceWindow],
        crew: Sequence[CrewDuty],
        p90: bool,
    ) -> list[Conflict]:
        def span(o: Occupation) -> tuple[dt.datetime, dt.datetime]:
            return (o.start90, o.end90) if p90 else (o.start, o.end)

        out: dict[Key, Conflict] = {}

        def report(c: Conflict) -> None:
            if c.key not in out or c.time < out[c.key].time:
                out[c.key] = c

        by_res: dict[tuple[str, str], list[Occupation]] = defaultdict(list)
        for o in occs:
            by_res[(o.kind, o.resource)].append(o)
        for (kind, res), group in by_res.items():
            if kind == "block":
                self._segment(res, group, span, now, maintenance, report)
            else:
                self._station(res, group, span, now, report)
        for c in list(out.values()):
            if c.type in (HEAD_ON, CROSSING):
                self._deadlock(c, occs, span, now, maintenance, report)
        self._crew(occs, crew, span, now, report)
        return list(out.values())

    def _usable(self, seg: str, at: dt.datetime, maintenance: Sequence[MaintenanceWindow]) -> list[Line]:
        closed = {w.line for w in maintenance if w.segment == seg and w.start <= at < w.end}
        if None in closed:
            return []
        return [ln for ln in self.resources.lines.get(seg, []) if ln.line_id not in closed]

    def _segment(
        self,
        seg: str,
        group: list[Occupation],
        span: Span,
        now: dt.datetime,
        maintenance: Sequence[MaintenanceWindow],
        report: Report,
    ) -> None:
        lines_all = self.resources.lines.get(seg, [])
        extra = (
            dt.timedelta(seconds=self.clearance_s)
            if lines_all and all(ln.absolute for ln in lines_all)
            else None
        )

        def held(o: Occupation) -> tuple[dt.datetime, dt.datetime]:  # absolute block: until line clear
            a, b = span(o)
            return (a, b + extra) if extra else (a, b)

        points = sorted({span(o)[0] for o in group})
        for at in points:
            here = [o for o in group if held(o)[0] <= at < held(o)[1]]
            if not here:
                continue
            lines = self._usable(seg, at, maintenance)
            dn = [o for o in here if o.direction == "DN"]
            up = [o for o in here if o.direction == "UP"]
            for o in here:  # a train through a segment closed on all its usable lines (type 7)
                if not [ln for ln in lines if ln.direction in (o.direction, "BOTH")]:
                    report(self._conflict(RULE, (o,), seg, at, now, "segment closed for maintenance"))
            if not self._fits(lines, len(dn), len(up)):
                if dn and up and not self._fits(lines, 1, 1):  # even one train each way cannot pass
                    pair = (dn[0], up[0])
                    committed = all(o.committed for o in pair)
                    c = self._conflict(HEAD_ON, pair, seg, at, now, "opposing trains on the only usable line")
                    if committed:
                        c.status = "NO_FEASIBLE_RESOLUTION"
                    report(c)
                else:  # the shortfall is in one direction: its trains follow too closely
                    same = dn if not self._fits(lines, len(dn), 0) else up
                    report(self._conflict(FOLLOWING, tuple(same[:2]), seg, at, now, "more trains than lines"))
            # automatic block: a follower closer than the line-clear time
            for d in (dn, up):
                if len(d) > 1 and not all(ln.absolute for ln in lines):
                    first, second = sorted(d, key=lambda o: span(o)[0])[:2]
                    gap = (span(second)[0] - span(first)[0]).total_seconds()
                    if 0 <= gap < self.clearance_s:
                        report(
                            self._conflict(FOLLOWING, (first, second), seg, span(second)[0], now, "headway")
                        )

    @staticmethod
    def _fits(lines: Sequence[Line], n_dn: int, n_up: int) -> bool:
        """Can ``n_dn`` and ``n_up`` trains share the segment's usable lines at one instant?"""
        dn_only = [ln for ln in lines if ln.direction == "DN"]
        up_only = [ln for ln in lines if ln.direction == "UP"]
        both = [ln for ln in lines if ln.direction == "BOTH"]
        for k in range(len(both) + 1):  # k reversible lines to DN, the rest to UP
            if _capacity(dn_only + both[:k], n_dn) and _capacity(up_only + both[k:], n_up):
                return True
        return False

    def _station(
        self, station: str, group: list[Occupation], span: Span, now: dt.datetime, report: Report
    ) -> None:
        lines = self.resources.stations.get(station, [])
        standing = [o for o in group if o.stop]
        for at in sorted({span(o)[0] for o in standing}):
            here = [o for o in standing if span(o)[0] <= at < span(o)[1]]
            if len(here) < 2:
                continue
            if not _assignable(here, lines):
                report(
                    self._conflict(
                        CROSSING, tuple(here[:2]), station, at, now, "no free line of sufficient CSR"
                    )
                )
            pax = [o for o in here if o.passenger]
            platforms = [ln for ln in lines if ln.platform and ln.passenger_ok]
            booked: dict[str, list[Occupation]] = defaultdict(list)
            for o in pax:
                if o.platform:
                    booked[o.platform].append(o)
            clash = next((v for v in booked.values() if len(v) > 1), None)
            if clash is not None or (pax and len(pax) > len(platforms)):
                pair = tuple((clash or pax)[:2])
                report(self._conflict(PLATFORM, pair, station, at, now, "platform"))

    def _deadlock(
        self,
        c: Conflict,
        occs: Sequence[Occupation],
        span: Span,
        now: dt.datetime,
        maintenance: Sequence[MaintenanceWindow],
        report: Report,
    ) -> None:
        """Wait-for graph at the conflict instant: each train waits for the holders of its next resource."""
        at = c.time - EPSILON  # just before: every train still holds what it holds
        holding: dict[str, Occupation] = {}
        nxt: dict[str, Occupation] = {}
        for run_id, seq in itertools.groupby(
            sorted(occs, key=lambda o: (o.run_id, o.seq)), key=lambda o: o.run_id
        ):
            items = list(seq)
            for i, o in enumerate(items):
                if span(o)[0] <= at < span(o)[1]:
                    holding[run_id] = o
                    if i + 1 < len(items):
                        nxt[run_id] = items[i + 1]
        holders: dict[tuple[str, str], list[str]] = defaultdict(list)
        for run_id, o in holding.items():
            holders[(o.kind, o.resource)].append(run_id)
        waits: dict[str, set[str]] = defaultdict(set)
        for run_id, want in nxt.items():
            others = [h for h in holders[(want.kind, want.resource)] if h != run_id]
            if not others:
                continue
            if want.kind == "station":
                full = len(holders[(want.kind, want.resource)]) >= len(
                    self.resources.stations.get(want.resource, [])
                )
            else:  # the segment it wants is held against it by opposing trains
                opposite = [h for h in others if holding[h].direction != want.direction]
                n_dn = 1 if want.direction == "DN" else len(opposite)
                n_up = 1 if want.direction == "UP" else len(opposite)
                lines = self._usable(want.resource, at, maintenance)
                full = bool(opposite) and not self._fits(lines, n_dn, n_up)
            if full:
                waits[run_id].update(others)
        cycle = _cycle(waits)
        if cycle:
            group = tuple(holding[r] for r in cycle)
            d = self._conflict(DEADLOCK, group, c.resource, c.time, now, "cycle in the wait-for graph")
            if all(o.committed for o in group):
                d.status = "NO_FEASIBLE_RESOLUTION"
            report(d)

    def _crew(
        self,
        occs: Sequence[Occupation],
        crew: Sequence[CrewDuty],
        span: Span,
        now: dt.datetime,
        report: Report,
    ) -> None:
        last: dict[str, Occupation] = {}
        for o in occs:
            if o.run_id not in last or span(o)[0] > span(last[o.run_id])[0]:
                last[o.run_id] = o
        for duty in crew:
            end = last.get(duty.run_id)
            expiry = duty.sign_on + duty.limit
            if end is not None and span(end)[0] > expiry:
                report(self._conflict(RULE, (end,), "crew", expiry, now, "crew duty expires en route"))

    # ------------------------------------------------------------------ records
    def _conflict(
        self,
        ctype: int,
        occs: Sequence[Occupation],
        resource: str,
        at: dt.datetime,
        now: dt.datetime,
        detail: str,
    ) -> Conflict:
        trains = tuple(sorted({o.run_id for o in occs}))
        overlap = max((o.end - at).total_seconds() for o in occs) if occs else 0.0
        low = min((self.weights.get(self.directory.priority_class(r), 1.0) for r in trains), default=1.0)
        return Conflict(
            ctype, trains, resource, max(at, now), now, impact_s=max(0.0, overlap) * low, detail=detail
        )

    def _priority(self, c: Conflict) -> str:
        if c.status == "NO_FEASIBLE_RESOLUTION":
            return "P2"
        if c.near and c.lead_time_s >= self.urgent_s:
            return "P3"
        return BASE_PRIORITY[c.type]


def _capacity(lines: Sequence[Line], n: int) -> bool:
    if n == 0:
        return True
    if not lines:
        return False
    if any(not ln.absolute for ln in lines):  # automatic block: following trains share a line
        return True
    return n <= len(lines)


def _assignable(trains: Sequence[Occupation], lines: Sequence[StationLine]) -> bool:
    """Greedy: longest trains first onto the longest lines that hold them."""
    free = sorted(lines, key=lambda ln: ln.csr_m or float("inf"))
    for o in sorted(trains, key=lambda o: -(o.length_m or 0)):
        fit = next((ln for ln in free if (ln.csr_m or float("inf")) >= (o.length_m or 0)), None)
        if fit is None:
            return False
        free.remove(fit)
    return True


def _cycle(waits: dict[str, set[str]]) -> list[str]:
    """One cycle of the wait-for graph (sorted for determinism), or []."""
    colour: dict[str, int] = {}
    stack: list[str] = []

    def visit(u: str) -> list[str]:
        colour[u] = 1
        stack.append(u)
        for v in sorted(waits.get(u, ())):
            if colour.get(v) == 1:
                return stack[stack.index(v) :]
            if v not in colour:
                found = visit(v)
                if found:
                    return found
        colour[u] = 2
        stack.pop()
        return []

    for u in sorted(waits):
        if u not in colour:
            found = visit(u)
            if found:
                return sorted(found)
    return []


# --------------------------------------------------------------------------- scheduling and stability
@dataclass
class DetectionSchedule:
    """FR-CD-04: run on every state event and plan change, and at least every ``max_interval_s``."""

    max_interval_s: float
    runs: list[dt.datetime] = field(default_factory=list)

    @classmethod
    def from_register(cls, register: Register | None = None) -> DetectionSchedule:
        return cls(float((register or load()).value("cd.rerun_max_interval_s")))

    def due(self, now: dt.datetime, state_event: bool = False, plan_change: bool = False) -> bool:
        if state_event or plan_change or not self.runs:
            return True
        return (now - self.runs[-1]).total_seconds() >= self.max_interval_s

    def record(self, now: dt.datetime) -> None:
        self.runs.append(now)

    @property
    def max_gap_s(self) -> float:
        return max(((b - a).total_seconds() for a, b in itertools.pairwise(self.runs)), default=0.0)


@dataclass
class StabilityTracker:
    """FR-CD-07: how often a conflict appears and then disappears without action, and flips back."""

    active: dict[Key, dt.datetime] = field(default_factory=dict)
    seen: set[Key] = field(default_factory=set)
    gone: set[Key] = field(default_factory=set)
    appeared: int = 0
    vanished_without_action: int = 0
    resolved_by_action: int = 0
    flip_flops: int = 0
    lifetimes_s: list[float] = field(default_factory=list)
    runs: int = 0

    def update(self, now: dt.datetime, conflicts: Iterable[Conflict], action: bool = False) -> None:
        self.runs += 1
        current = {c.key: c for c in conflicts}
        for key in current.keys() - self.active.keys():
            self.appeared += 1
            if key in self.gone:
                self.flip_flops += 1
            self.active[key] = now
            self.seen.add(key)
        for key in self.active.keys() - current.keys():
            started = self.active.pop(key)
            self.lifetimes_s.append((now - started).total_seconds())
            self.gone.add(key)
            if action:
                self.resolved_by_action += 1
            else:
                self.vanished_without_action += 1

    def metrics(self) -> dict[str, float]:
        unique = len(self.seen)
        return {
            "runs": self.runs,
            "unique_conflicts": unique,
            "appearances": self.appeared,
            "vanished_without_action": self.vanished_without_action,
            "resolved_by_action": self.resolved_by_action,
            "flip_flops": self.flip_flops,
            "flip_flop_rate": self.flip_flops / unique if unique else 0.0,
            "mean_lifetime_s": sum(self.lifetimes_s) / len(self.lifetimes_s) if self.lifetimes_s else 0.0,
        }


def stability_report(per_scenario: dict[str, dict[str, float]]) -> str:
    """Alert-stability metrics per scenario as a Markdown table (FR-CD-07)."""
    cols = [
        "runs",
        "unique_conflicts",
        "appearances",
        "vanished_without_action",
        "flip_flops",
        "mean_lifetime_s",
    ]
    lines = ["| Scenario | " + " | ".join(cols) + " |", "|---|" + "---:|" * len(cols)]
    for name, m in per_scenario.items():
        lines.append(f"| {name} | " + " | ".join(f"{m[c]:g}" for c in cols) + " |")
    return "\n".join(lines) + "\n"
