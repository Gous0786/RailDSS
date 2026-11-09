"""Meso-level movement simulator, policy BL-0 (spec FR-TWIN-10/12/14, §12.2; issue #20).

A SimPy discrete-event simulation of one day on the published network ``KTV-PSA-2025-08``:

* **Unit resources.** On VZM-PSA (absolute block) a COA block line is one resource; on KTV-VZM
  (automatic block) each synthetic sub-block of a line is one. Station lines are resources too. A
  block's line is shared by both directions of the block id (see :mod:`raildss_twin.layout`).
* **Movement.** A train holds its station line, asks for a block line (reception and despatch only
  along Connections edges), and when it has one runs the block in the calibrated running time. The
  line it left is released a clearing time later, (train length + adequate distance) over the
  clearing speed, so a block is free only after the previous train clears plus adequate distance.
* **BL-0.** Booked order, first come first served: SimPy queues are FIFO; a train takes the nearest
  free permitted line (platform, then main, then loop; the own-direction block line before MID).
  Passenger trains never leave a stop before the booked departure; freight never stops by plan.
* **Deadlocks** are detected (a set of waiting trains whose every wanted resource is held by a
  member) and reported with their location; they are never hidden.
* **Ground truth.** Every state, occupancy, route / signal and delay record is an
  :class:`EventEnvelope` for ``gt.*`` (evaluation only, FR-TWIN-14), hashed in emission order; the
  same plans and seed give the same hash.

Stochastic perturbations, the observation model, the injector and the station-master agents are M1.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import uuid
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import simpy
from pydantic import BaseModel

from raildss_common.envelope import EventEnvelope, EventType
from raildss_params import Register
from raildss_schema.entities import StationLine
from raildss_twin.layout import BlockLine, Layout
from raildss_twin.plans import SECONDS_PER_DAY, Node, Plan, Timing

SOURCE = "SIM.TWIN"
SECTION = "KTV-PSA"
KMH_PER_MPS = 3.6
SECONDS_PER_HOUR = 3600
M_PER_KM = 1000


def token(run_id: str) -> str:
    """Run ids of freight loads contain a space; the envelope allows none."""
    return run_id.replace(" ", "_")


Sink = Callable[[str, EventEnvelope], None]
Proc = Generator[Any, Any, Any]


class StuckTrain(BaseModel):
    run_id: str
    kind: str
    location: str
    waiting_for: str | None
    held: list[str]
    blocked_by: list[str]
    reason: str


class TrainPosition(BaseModel):
    """Where a train that is running is at one instant: on a block (``block`` set), or at a station."""

    run_id: str
    kind: str
    status: str
    station: str  # the station it is at, or has just left
    block: str | None  # COA block it is on, in its direction of travel (e.g. ``KUK-VZM``)
    towards: str | None  # the block's far station
    km: float
    held: list[str]  # unit resources it occupies


class Deadlock(BaseModel):
    time_s: float
    trains: list[StuckTrain]


class RunReport(BaseModel):
    network_version: str
    scenario_id: str
    seed: int
    planned: int
    entered: int
    exited: int
    in_progress: int
    stuck: list[StuckTrain]
    deadlocks: list[Deadlock]
    unsupported: list[dict[str, str]]
    csr_exceeded: int
    events: dict[str, int]
    trace_hash: str
    sim_end_s: float
    wall_s: float = 0.0

    @property
    def completed(self) -> bool:
        """Every planned train entered and exited, none is stuck, no deadlock."""
        return self.exited == self.planned and not self.stuck and not self.deadlocks


@dataclass
class Train:
    plan: Plan
    status: str = "pending"  # pending | running | waiting | dwelling | done | stuck
    op: str = ""
    block: str | None = None
    held: dict[str, Any] = field(default_factory=dict)  # resource label -> request
    gated: dict[str, str] = field(default_factory=dict)  # resource label -> reversible line it belongs to
    claims: dict[str, int] = field(default_factory=dict)  # reversible line -> claims (held or requested)
    waiting_gates: list[str] = field(
        default_factory=list
    )  # reversible lines it waits to be free of oncoming traffic
    waiting: list[tuple[str, simpy.Resource]] | None = None
    segment: tuple[float, float, float, float] | None = None  # t0, t1, km0, km1
    km: float = 0.0
    stood: bool = False
    stuck_reason: str = ""
    exit_s: float | None = None
    entered: bool = False


class Option(NamedTuple):
    """One resource a train may take: label, capacity and, for a reversible line, its direction gate."""

    label: str
    capacity: int = 1
    gate: str = ""
    direction: str = ""


class NoRouteError(RuntimeError):
    """No Connections edge permits the move (the train is reported stuck)."""


class Sim:
    def __init__(
        self,
        layout: Layout,
        timing: Timing,
        register: Register,
        plans: list[Plan],
        day: dt.date,
        *,
        origin: dt.datetime,
        scenario_id: str,
        seed: int,
        sink: Sink | None = None,
        env: simpy.Environment | None = None,
        until_s: float | None = None,
        keep_events: bool = False,
    ) -> None:
        cfg = register.value("twin.sim")
        self.state_period = float(cfg["state_period_s"])
        self.min_dwell = float(cfg["min_dwell_s"])
        self.clearing_mps = float(cfg["clearing_speed_kmh"]) / KMH_PER_MPS
        self.exit_clear = float(cfg["exit_clear_s"])
        self.grace_s = float(cfg["horizon_grace_h"]) * SECONDS_PER_HOUR
        self.monitor_every = max(1, round(float(cfg["monitor_period_s"]) / self.state_period))
        self.adequate_m = float(register.value("block.adequate_distance_m.macls"))
        self.layout, self.timing = layout, timing
        self.env = env or simpy.Environment()
        self.origin, self.day, self.scenario_id, self.seed = origin, day, scenario_id, seed
        self.sink = sink
        self.trains = [Train(p) for p in plans]
        self.resources: dict[str, simpy.Resource] = {}
        self.owner: dict[Any, Train] = {}
        self.gates: dict[str, tuple[str, set[str]]] = {}
        self._gate_events: dict[str, simpy.Event] = {}
        self.seq = 0
        self.hash = hashlib.sha256()
        self.counts: dict[str, int] = {}
        self.events: list[EventEnvelope] = []
        self.keep_events = keep_events
        self.occupancy_log: list[tuple[float, str, str, bool]] = []
        self.deadlocks: list[Deadlock] = []
        self._deadlock_seen: set[frozenset[str]] = set()
        self.csr_exceeded = 0
        last_plan = max((t.plan.nodes[-1].dep for t in self.trains), default=0.0)
        self.windowed = until_s is not None
        self.until_s = until_s if until_s is not None else max(last_plan, SECONDS_PER_DAY) + self.grace_s
        self.stop = self.env.event()
        self.pending = len(self.trains)

    # ------------------------------------------------------------------ events
    def emit(
        self,
        topic: str,
        etype: EventType,
        train: Train | None,
        payload: dict[str, Any],
        km: float | None = None,
        ne_id: str | None = None,
    ) -> None:
        self.seq += 1
        when = self.origin + dt.timedelta(seconds=self.env.now)
        event = EventEnvelope(
            event_id=uuid.uuid5(
                uuid.NAMESPACE_URL, f"raildss-twin/{self.seed}/{self.scenario_id}/{self.seq}"
            ),
            event_type=etype,
            schema_version="1.0",
            source=SOURCE,
            section_id=SECTION,
            network_version=self.layout.network_version,
            scenario_id=self.scenario_id,
            seq=self.seq,
            event_time=when,
            train_run_id=token(train.plan.run_id) if train else None,
            location=({"km": km, "ne_id": ne_id} if km is not None else None),  # type: ignore[arg-type]
            payload=payload,
        )
        self.hash.update(topic.encode("utf-8") + b"\0" + event.to_json() + b"\n")
        self.counts[topic] = self.counts.get(topic, 0) + 1
        if self.keep_events:
            self.events.append(event)
        if self.sink is not None:
            self.sink(topic, event)

    # ------------------------------------------------------------------ resources
    def resource(self, label: str, capacity: int = 1) -> simpy.Resource:
        r = self.resources.get(label)
        if r is None:
            r = self.resources[label] = simpy.Resource(self.env, capacity)
        return r

    def _granted(self, t: Train, opt: Option, req: Any) -> None:
        label = opt.label
        t.held[label] = req
        if opt.gate:
            t.gated[label] = opt.gate  # the gate was claimed when the request was made
        self.owner[req] = t
        self.occupancy_log.append((self.env.now, label, t.plan.run_id, True))
        self.emit("gt.occupancy", EventType.TDS_OCCUPANCY, t, {"tds_id": label, "occupied": True})

    def release(self, t: Train, label: str) -> None:
        req = t.held.pop(label, None)
        if req is None:
            return
        self.owner.pop(req, None)
        self.resources[label].release(req)
        gate = t.gated.pop(label, None)
        if gate:
            self._unclaim(t, gate)
        self.occupancy_log.append((self.env.now, label, t.plan.run_id, False))
        self.emit("gt.occupancy", EventType.TDS_OCCUPANCY, t, {"tds_id": label, "occupied": False})

    def release_later(self, t: Train, labels: list[str], delay: float) -> None:
        if not labels:
            return
        if delay <= 0:
            for label in labels:
                self.release(t, label)
            return

        def later() -> Proc:
            yield self.env.timeout(delay)
            for label in labels:
                self.release(t, label)

        self.env.process(later())

    def clearing_s(self, t: Train) -> float:
        return (t.plan.template.length_m + self.adequate_m) / self.clearing_mps

    def _claim(self, t: Train, opt: Option) -> None:
        """Reserve the direction of a reversible line. Call in the same step as the gate check and request,
        so a train at the same instant sees the claim; the check itself is :meth:`gate_ok`."""
        if not opt.gate:
            return
        assert self.gate_ok(opt)
        _, users = self.gates.setdefault(opt.gate, (opt.direction, set()))
        users.add(t.plan.run_id)
        t.claims[opt.gate] = t.claims.get(opt.gate, 0) + 1

    def _unclaim(self, t: Train, gate: str) -> None:
        left = t.claims[gate] - 1
        if left:
            t.claims[gate] = left
            return
        del t.claims[gate]
        users = self.gates[gate][1]
        users.discard(t.plan.run_id)
        if not users:
            del self.gates[gate]
            ev = self._gate_events.pop(gate, None)
            if ev is not None and not ev.triggered:
                ev.succeed()

    def gate_ok(self, opt: Option) -> bool:
        """A reversible line (MID) may be entered only if it is empty or runs the same way."""
        if not opt.gate:
            return True
        state = self.gates.get(opt.gate)
        return state is None or state[0] == opt.direction

    def gate_event(self, gate: str) -> simpy.Event:
        ev = self._gate_events.get(gate)
        if ev is None or ev.triggered:
            ev = self._gate_events[gate] = self.env.event()
        return ev

    def acquire(self, t: Train, options: list[Option]) -> Proc:
        """BL-0: take the first free permitted option, else wait for any. Returns its index.

        The direction of a reversible line is claimed in the same step as the check and the request, never
        after the process resumes, so two trains at the same instant cannot both enter it in opposite
        directions. A claim that is not used (cancelled or surplus request) is given back.
        """
        previous = t.status
        while True:
            for i, opt in enumerate(options):
                r = self.resource(opt.label, opt.capacity)
                if r.count < r.capacity and self.gate_ok(opt):
                    self._claim(t, opt)
                    req = r.request()
                    yield req
                    self._granted(t, opt, req)
                    t.waiting, t.waiting_gates, t.status = None, [], previous
                    return i
            t.stood = True
            t.status = "waiting"
            eligible = [(i, o) for i, o in enumerate(options) if self.gate_ok(o)]
            blocked = [o for o in options if not self.gate_ok(o)]
            t.waiting = [(o.label, self.resource(o.label, o.capacity)) for _, o in eligible]
            t.waiting_gates = sorted({o.gate for o in blocked})
            requests = []
            for _, o in eligible:
                self._claim(t, o)
                requests.append(self.resource(o.label, o.capacity).request())
            wakers = [self.gate_event(o.gate) for o in blocked]
            yield simpy.AnyOf(self.env, [*requests, *wakers])
            granted = [k for k, rq in enumerate(requests) if rq.triggered]
            keep = granted[0] if granted else None
            for k, rq in enumerate(requests):
                if k == keep:
                    continue
                other = eligible[k][1]
                if rq.triggered:
                    self.resource(other.label, other.capacity).release(rq)
                else:
                    rq.cancel()
                if other.gate:
                    self._unclaim(t, other.gate)
            t.waiting, t.waiting_gates = None, []
            if keep is not None:
                i, opt = eligible[keep]
                self._granted(t, opt, requests[keep])
                t.status = previous
                return i

    # ------------------------------------------------------------------ station and block lines
    def station_candidates(
        self, t: Train, node: Node, incoming: BlockLine | None, hop: tuple[str, str] | None, stands: bool
    ) -> list[StationLine]:
        direction = self.layout.direction(*hop) if hop else None
        passenger = t.plan.kind == "passenger"
        nxt = self.layout.block_by_pair[hop] if hop else None
        out: list[StationLine] = []
        for sl in self.layout.station_lines.get(node.op, []):
            if sl.kind not in ("main", "loop") or sl.status != "in_use":
                continue
            if not (sl.passenger_ok if passenger else sl.goods_ok):
                continue
            if incoming is not None and (incoming.block_id, incoming.line_id) not in self.layout.receive.get(
                (node.op, sl.line_no), set()
            ):
                continue
            if nxt is not None and direction is not None:
                sends = self.layout.send.get((node.op, sl.line_no), set())
                if not any((nxt.id, ln.line_id) in sends for ln in self.layout.lines_for(nxt.id, direction)):
                    continue
            out.append(sl)

        def rank(sl: StationLine) -> tuple[int, int]:
            if passenger and stands:
                order = 0 if sl.platform else 1 if sl.kind == "main" else 2
            else:
                order = 0 if sl.kind == "main" else 1
            return (order, sl.line_no)

        return sorted(out, key=rank)

    def block_options(self, t: Train, a: str, b: str, sl: StationLine) -> list[BlockLine]:
        block = self.layout.block_by_pair[(a, b)]
        direction = self.layout.direction(a, b)
        sends = self.layout.send.get((a, sl.line_no), set())
        return [ln for ln in self.layout.lines_for(block.id, direction) if (block.id, ln.line_id) in sends]

    # ------------------------------------------------------------------ the train process
    def km_of(self, op: str) -> float:
        return self.layout.km(op)

    def run_train(self, t: Train) -> Proc:
        plan, layout = t.plan, self.layout
        yield self.env.timeout(max(0.0, plan.entry_s - self.env.now))
        self.pending -= 1
        t.entered, t.status = True, "running"
        idx = [i for i, n in enumerate(plan.nodes) if not layout.ops[n.op].is_halt]
        # A train booked before the start of the day is already running: it resumes inside the block it is
        # on at the start, on the booked plan, instead of entering at a station.
        resume_k = self.resume_hop(plan, idx) if plan.entry_s < self.env.now else None
        incoming: BlockLine | None = None
        incoming_labels: list[str] = []
        try:
            for k, ni in enumerate(idx):
                if resume_k is not None and k < resume_k:
                    continue
                node = plan.nodes[ni]
                last = k == len(idx) - 1
                if resume_k == k and not last:
                    incoming = yield from self._resume_in_block(t, k, idx)
                    incoming_labels = [lab for lab in t.held if lab.startswith(("BL:", "SB:"))]
                    resume_k = None
                    continue
                resume_k = None
                hop = None if last else (node.op, plan.nodes[idx[k + 1]].op)
                t.op, t.block, t.segment, t.km = node.op, None, None, self.km_of(node.op)
                # ---- reception: take a station line, then release the block it came by
                stands = node.stop or last
                cands = self.station_candidates(t, node, incoming, hop, stands)
                if not cands:
                    raise NoRouteError(f"no station line at {node.op} permits the move")
                fit = [sl for sl in cands if (sl.csr_m or float("inf")) >= plan.template.length_m]
                ordered = (fit + [sl for sl in cands if sl not in fit]) if stands else cands
                i = yield from self.acquire(
                    t, [Option(f"SL:{node.op}/{sl.line_no}", sl.capacity_trains) for sl in ordered]
                )
                sl = ordered[i]
                if stands and sl not in fit:
                    self.csr_exceeded += 1
                self.emit(
                    "gt.signal",
                    EventType.ROUTE_SET,
                    t,
                    {"station": node.op, "line": sl.line_no, "mode": "receive"},
                    self.km_of(node.op),
                    node.op,
                )
                self.release_later(t, incoming_labels, self.clearing_s(t))
                incoming_labels = []
                arrived = self.env.now
                if plan.timetabled:
                    self._delay(t, node, "arrival", arrived - node.arr)
                if last:
                    t.status = "dwelling"
                    yield self.env.timeout(self.exit_clear)
                    self.release(t, f"SL:{node.op}/{sl.line_no}")
                    t.status, t.exit_s = "done", self.env.now
                    return
                # ---- dwell
                if node.stop:
                    t.status = "dwelling"
                    t.stood = True
                    yield self.env.timeout(max(self.min_dwell, node.dep - arrived, 0.0))
                    t.status = "running"
                if plan.timetabled and node.stop:
                    self._delay(t, node, "departure", self.env.now - node.dep)
                # ---- despatch: take a block line, then free the station line
                assert hop is not None
                lines = self.block_options(t, *hop, sl)
                if not lines:
                    raise NoRouteError(
                        f"no block line from {hop[0]} to {hop[1]} permitted by line {sl.line_no}"
                    )
                first = [self._line_resource(ln, hop) for ln in lines]
                j = yield from self.acquire(t, first)
                chosen = lines[j]
                self.emit(
                    "gt.signal",
                    EventType.ROUTE_SET,
                    t,
                    {
                        "station": node.op,
                        "line": sl.line_no,
                        "mode": "send",
                        "block": chosen.block_id,
                        "block_line": chosen.line_id,
                    },
                    self.km_of(node.op),
                    node.op,
                )
                self.release_later(t, [f"SL:{node.op}/{sl.line_no}"], self.clearing_s(t))
                yield from self._traverse(t, k, idx, chosen)
                incoming = chosen
                incoming_labels = [lab for lab in t.held if lab.startswith("BL:") or lab.startswith("SB:")]
        except NoRouteError as exc:
            t.status, t.stuck_reason = "stuck", str(exc)
            for label in list(t.held):
                self.release(t, label)

    def resume_hop(self, plan: Plan, idx: list[int]) -> int:
        """The hop (index into the block stations) a train booked before now is on: the first ending ahead."""
        for k in range(len(idx) - 1):
            if plan.nodes[idx[k + 1]].arr > self.env.now:
                return k
        return len(idx) - 1

    def _resume_progress(self, plan: Plan, k: int, idx: list[int]) -> float:
        """Fraction of the block already run now, by the booked times at its two ends."""
        a, b = plan.nodes[idx[k]], plan.nodes[idx[k + 1]]
        span = b.arr - a.dep
        return min(1.0, max(0.0, (self.env.now - a.dep) / span)) if span > 0 else 0.0

    @staticmethod
    def _resume_sub_block(subs: list[tuple[str, float]], progress: float) -> int:
        length = sum(m for _, m in subs)
        done = 0.0
        for n, (_, metres) in enumerate(subs):
            done += metres
            if done > progress * length:
                return n
        return len(subs) - 1

    def _resume_option(self, ln: BlockLine, progress: float) -> Option:
        """What a resumed train holds: the whole line, or the sub-block at ``progress`` of the block."""
        if ln.system == "AUTO":
            subs = self.layout.canonical_sub_blocks(ln.block_id, ln.line_id)
            if subs:
                return self._option(ln, f"SB:{subs[self._resume_sub_block(subs, progress)][0]}")
        return self._option(ln, f"BL:{ln.key}")

    def _resume_in_block(self, t: Train, k: int, idx: list[int]) -> Proc:
        """Place a train already between block stations ``k`` and ``k + 1``; run the rest of the block."""
        plan, layout = t.plan, self.layout
        a, b = plan.nodes[idx[k]], plan.nodes[idx[k + 1]]
        block = layout.block_by_pair[(a.op, b.op)]
        progress = self._resume_progress(plan, k, idx)
        lines = layout.lines_for(block.id, layout.direction(a.op, b.op))
        t.op, t.block, t.km = a.op, block.id, self.km_of(a.op)
        j = yield from self.acquire(t, [self._resume_option(ln, progress) for ln in lines])
        yield from self._traverse(t, k, idx, lines[j], resume_progress=progress)
        return lines[j]

    def run_time(self, t: Train, x: Node, y: Node, start_stopped: bool, end_stopped: bool) -> float:
        """Calibrated running time. A booked train does not pass a point before its booked time: where the
        timetable is padded it runs to time (a stop is simply reached early and waits for its departure)."""
        span = self.timing.leg(t.plan, x.op, y.op, start_stopped, end_stopped)
        if t.plan.timetabled and not y.stop:
            span = max(span, y.arr - self.env.now)
        return span

    def _option(self, ln: BlockLine, label: str) -> Option:
        if ln.direction != "BOTH":
            return Option(label)
        return Option(label, 1, ln.key, self.layout.blocks[ln.block_id].direction)

    def _line_resource(self, ln: BlockLine, hop: tuple[str, str]) -> Option:
        if ln.system == "AUTO":
            subs = self.layout.canonical_sub_blocks(ln.block_id, ln.line_id)
            if subs:
                return self._option(ln, f"SB:{subs[0][0]}")
        return self._option(ln, f"BL:{ln.key}")

    def _traverse(
        self, t: Train, k: int, idx: list[int], line: BlockLine, resume_progress: float | None = None
    ) -> Proc:
        plan, layout = t.plan, self.layout
        a, b = plan.nodes[idx[k]], plan.nodes[idx[k + 1]]
        block = layout.blocks[line.block_id]
        route = [a, *plan.nodes[idx[k] + 1 : idx[k + 1]], b]
        stopped_at = [n.stop or (n is a and t.stood) for n in route]
        stopped_at[-1] = b.stop or (idx[k + 1] == len(plan.nodes) - 1 and plan.timetabled)
        t.stood = False
        subs = layout.canonical_sub_blocks(line.block_id, line.line_id) if line.system == "AUTO" else []
        t.block = block.id
        if subs:
            total_s = self.run_time(t, a, b, stopped_at[0], stopped_at[-1])
            length = sum(m for _, m in subs)
            km0, km1 = self.km_of(a.op), self.km_of(b.op)
            start_m = 0.0 if resume_progress is None else resume_progress * length
            first = 0 if resume_progress is None else self._resume_sub_block(subs, resume_progress)
            done_m = 0.0  # metres at the start of the current sub-block
            for n, (key, metres) in enumerate(subs):
                label = f"SB:{key}"
                begin = max(done_m, start_m) if n == first else done_m
                end = done_m + metres
                done_m = end
                if n < first:
                    continue
                if n > first:
                    prev = f"SB:{subs[n - 1][0]}"
                    yield from self.acquire(t, [self._option(line, label)])
                    self._signal(t, line, label)
                    self.release_later(t, [prev], self.clearing_s(t))
                else:
                    self._signal(t, line, label)
                span = total_s * (end - begin) / length
                t.segment = (
                    self.env.now,
                    self.env.now + span,
                    km0 + (km1 - km0) * begin / length,
                    km0 + (km1 - km0) * end / length,
                )
                yield self.env.timeout(span)
            return
        now0 = self.env.now
        live = resume_progress is None  # a resumed train skips the legs it has already run
        for x, y, sx, sy in zip(route, route[1:], stopped_at, stopped_at[1:], strict=False):
            if live:
                span = self.run_time(t, x, y, sx, sy)
            elif y.arr <= now0:  # this leg is behind the train
                if y is not b and y.stop and y.dep > now0:  # it is dwelling at this halt now
                    t.op, t.segment, t.km = y.op, None, self.km_of(y.op)
                    t.status = "dwelling"
                    yield self.env.timeout(y.dep - now0)
                    t.status = "running"
                    live = True
                continue
            else:
                live = True
                span = max(0.0, y.arr - now0)  # the rest of this leg, on the booked plan
            t.segment = (self.env.now, self.env.now + span, self.km_of(x.op), self.km_of(y.op))
            yield self.env.timeout(span)
            if y is not b and y.stop:  # a halt inside the block
                t.op, t.segment, t.km = y.op, None, self.km_of(y.op)
                arrived = self.env.now
                if plan.timetabled:
                    self._delay(t, y, "arrival", arrived - y.arr)
                t.status = "dwelling"
                yield self.env.timeout(max(self.min_dwell, y.dep - arrived, 0.0))
                t.status = "running"
                if plan.timetabled:
                    self._delay(t, y, "departure", self.env.now - y.dep)

    def _signal(self, t: Train, line: BlockLine, label: str) -> None:
        key = label.split("#")[-1]
        sid = self.layout.entry_signal(line.block_id, line.line_id, int(key)) if key.isdigit() else None
        if sid:
            self.emit("gt.signal", EventType.SIGNAL_ASPECT, t, {"signal_id": sid, "aspect": "DANGER"})

    def _delay(self, t: Train, node: Node, kind: str, seconds: float) -> None:
        self.emit(
            "gt.delay",
            EventType.DELAY_RECORD,
            t,
            {"station": node.op, "kind": kind, "delay_s": round(seconds, 1), "primary": False},
            self.km_of(node.op),
            node.op,
        )

    # ------------------------------------------------------------------ monitor
    def position(self, t: Train) -> tuple[float, float]:
        if t.segment is not None:
            t0, t1, k0, k1 = t.segment
            if t1 > t0 and t0 <= self.env.now <= t1:
                f = (self.env.now - t0) / (t1 - t0)
                return k0 + (k1 - k0) * f, abs(k1 - k0) * M_PER_KM / (t1 - t0) * KMH_PER_MPS
        return t.km, 0.0

    def monitor(self) -> Proc:
        n = 0
        while True:
            for t in self.trains:
                if t.entered and t.status not in ("done", "stuck"):
                    km, speed = self.position(t)
                    self.emit(
                        "gt.state",
                        EventType.TRAIN_STATE,
                        t,
                        {"speed_kmh": round(speed, 1), "status": t.status, "station": t.op},
                        round(km, 3),
                        t.block or t.op,
                    )
            n += 1
            if n % self.monitor_every == 0:
                self.check_deadlock()
            active = [t for t in self.trains if t.entered and t.status not in ("done", "stuck")]
            if not active and not self.pending:
                self.stop.succeed()
                return
            if active and not self.pending and all(t.waiting is not None for t in active):
                self.check_deadlock()
                if self.deadlocks and len(self.deadlocked(active)) == len(active):
                    self.stop.succeed()
                    return
            yield self.env.timeout(self.state_period)

    def _can_proceed(self, t: Train, stuck: dict[str, Train]) -> bool:
        """True if something ``t`` waits for is free, or held by a train that is not itself stuck."""
        for _, r in t.waiting or []:
            holders = [self.owner.get(req) for req in r.users]
            if r.count < r.capacity or any(h is None or h.plan.run_id not in stuck for h in holders):
                return True
        for gate in t.waiting_gates:
            state = self.gates.get(gate)
            if state is None or any(u not in stuck for u in state[1] if u != t.plan.run_id):
                return True
        return False

    def deadlocked(self, active: list[Train]) -> list[Train]:
        """Waiting trains whose every wanted resource (or oncoming-free line) is held only by such trains."""
        w = {t.plan.run_id: t for t in active if t.waiting is not None}
        changed = True
        while changed:
            changed = False
            for key, t in list(w.items()):
                if self._can_proceed(t, w):
                    del w[key]
                    changed = True
        return list(w.values())

    def stuck_info(self, t: Train, reason: str) -> StuckTrain:
        waiting = t.waiting
        blocked_by = {
            self.owner[req].plan.run_id for _, r in (waiting or []) for req in r.users if req in self.owner
        }
        for gate in t.waiting_gates:
            blocked_by |= self.gates.get(gate, ("", set()))[1] - {t.plan.run_id}
        wanted = [lab for lab, _ in waiting or []] + [f"{g} (clear of oncoming)" for g in t.waiting_gates]
        return StuckTrain(
            run_id=t.plan.run_id,
            kind=t.plan.kind,
            location=f"{t.block} (towards {t.op})" if t.block else t.op,
            waiting_for=" or ".join(wanted) if wanted else None,
            held=sorted(t.held),
            blocked_by=sorted(blocked_by),
            reason=reason,
        )

    def check_deadlock(self) -> None:
        active = [t for t in self.trains if t.entered and t.status not in ("done", "stuck")]
        dead = self.deadlocked(active)
        ids = frozenset(t.plan.run_id for t in dead)
        if dead and ids not in self._deadlock_seen:
            self._deadlock_seen.add(ids)
            self.deadlocks.append(
                Deadlock(time_s=self.env.now, trains=[self.stuck_info(t, "deadlock") for t in dead])
            )

    # ------------------------------------------------------------------ run
    def run(self) -> RunReport:
        for t in self.trains:
            self.env.process(self.run_train(t))
        self.env.process(self.monitor())

        def horizon() -> Proc:
            yield self.env.timeout(self.until_s)
            if not self.stop.triggered:
                self.stop.succeed()

        self.env.process(horizon())
        self.env.run(until=self.stop)
        return self.report()

    def positions(self) -> list[TrainPosition]:
        """Every train that has entered and not left, placed on its current block or station line."""
        out = []
        for t in self.trains:
            if not t.entered or t.status in ("done", "stuck"):
                continue
            km, _ = self.position(t)
            block = self.layout.blocks.get(t.block) if t.block else None
            out.append(
                TrainPosition(
                    run_id=t.plan.run_id,
                    kind=t.plan.kind,
                    status=t.status,
                    station=t.op,
                    block=block.id if block else None,
                    towards=block.to_op if block else None,
                    km=round(km, 3),
                    held=sorted(t.held),
                )
            )
        return out

    def report(self) -> RunReport:
        windowed = self.windowed and not self.deadlocks
        stuck: list[StuckTrain] = []
        in_progress = 0
        for t in self.trains:
            if t.status == "done":
                continue
            if t.status == "stuck":
                stuck.append(self.stuck_info(t, t.stuck_reason))
            elif t.entered and not windowed:
                stuck.append(self.stuck_info(t, "still inside the section at the end of the run"))
            elif t.entered:
                in_progress += 1
            elif not windowed:
                stuck.append(self.stuck_info(t, "never entered"))
        return RunReport(
            network_version=self.layout.network_version,
            scenario_id=self.scenario_id,
            seed=self.seed,
            planned=len(self.trains),
            entered=sum(t.entered for t in self.trains),
            exited=sum(t.status == "done" for t in self.trains),
            in_progress=in_progress,
            stuck=stuck,
            deadlocks=self.deadlocks,
            unsupported=[],
            csr_exceeded=self.csr_exceeded,
            events=dict(sorted(self.counts.items())),
            trace_hash=self.hash.hexdigest(),
            sim_end_s=self.env.now,
        )


def occupancy_violations(
    log: list[tuple[float, str, str, bool]], capacity: Callable[[str], int]
) -> list[str]:
    """Invariant over the ground-truth trace: no unit resource holds more trains than its capacity."""
    held: dict[str, set[str]] = {}
    problems: list[str] = []
    for time_s, label, train, occupied in log:
        users = held.setdefault(label, set())
        if occupied:
            users.add(train)
            if len(users) > capacity(label):
                problems.append(f"{label} holds {sorted(users)} at {time_s:.0f} s")
        else:
            users.discard(train)
    return problems
