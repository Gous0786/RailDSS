"""Solver-facing intermediate representation, DISPLIB-style (spec FR-OPT-01, FR-TWIN-19, REC-02; issue #22).

A network version plus a day instance (or a window of it) becomes an :class:`Instance`:

* **Trains** are lists of **operations**. An operation is one train in one resource for a while:
  a station line (``station``), an absolute-block line (``block``) or an automatic-block sub-block
  (``sub_block``). Each has a minimum duration, a release time (``start_lb``), a latest start
  (``start_ub``) and **successors**, so the alternatives of the layout are branches of the train's
  graph: one station-line operation per permitted line, one chain per permitted block line. A
  successor edge exists only where a Connections edge permits it (receive: block line to station
  line; send: station line to block line), so a path through the graph is a permitted route.
* **Resources** have capacity 1 (station lines may carry more where the data says so). A reversible
  MID line is one resource shared by both directions.
* **Semantics.** A chosen path assigns each operation a start time ``s``. Along an edge
  ``s_next >= s + min_duration + delay``. An operation occupies each of its resources from ``s``
  until the next operation's start plus the resource's ``release_time`` (the clearing time of the
  line), or, for the last operation, until ``s + min_duration + release_time``. Occupations of one
  resource by different trains must not overlap beyond its capacity.
  ``docs/twin/displib-crosscheck.md`` compares this with the published DISPLIB 2025 specification;
  :meth:`Instance.to_displib` writes a document that follows it (one entry and one exit operation
  per train, plain successor indices, ``op_delay`` objective components).
* **Rolling windows** (issue #74). The IR is built from the plans and a :class:`Snapshot`, the part
  of the DSS state ``S_t`` it needs: what every train occupies at the window start. A train already
  running is **frozen** there: its graph starts at the operations it occupies, pinned to the times
  it took them, with no alternatives behind its head (the DISPLIB way of putting a train that is
  already in the network into an instance). ``snapshot_from_occupancy`` builds the snapshot from a
  TDS occupancy log (the twin's ground truth, or the DSS estimate once ``S_t`` is published, #71).
* **Objective.** Components ``coeff * max(0, start - threshold) + increment * [start > threshold]`` on
  operations: passenger arrival delay against the booked time at every stop and at the exit, and for
  freight the delay against unimpeded completion, weighted by the precedence class
  (``policy.weights``).

Times are integer seconds since the start of the simulated day. Durations and releases are rounded
down, so the instance is a relaxation by less than a second per operation.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from raildss_params import Register
from raildss_twin.layout import BlockLine, Layout
from raildss_twin.plans import Node, Plan, Timing
from raildss_twin.run import World, plans_for
from raildss_twin.sim import KMH_PER_MPS, SECONDS_PER_HOUR

FORMAT = "raildss-displib-ir"
VERSION = 1
DEFAULT_WEIGHT_CLASS = "freight_other"


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ResourceUse(Model):
    resource: str
    release_time: int


class Successor(Model):
    operation: int
    delay: int = 0


class Operation(Model):
    id: int
    visit: int  # index of the block station on the train's path
    node: str  # station of the visit
    kind: Literal["station", "block", "sub_block"]
    min_duration: int
    start_lb: int
    start_ub: int
    resources: list[ResourceUse]
    successors: list[Successor]
    block: str | None = None
    line: str | None = None


class Train(Model):
    id: str
    kind: str
    category: str
    operations: list[Operation]


class Resource(Model):
    id: str
    kind: Literal["station_line", "block_line", "sub_block"]
    capacity: int = 1


class Component(Model):
    """``coeff * max(0, start - threshold) + increment * [start > threshold]`` on one operation."""

    train: int
    operation: int
    coeff: float
    threshold: int
    increment: float = 0.0


class Occupation(Model):
    resource: str
    since_s: float


class TrainState(Model):
    run_id: str
    occupations: list[Occupation]  # resources held at the snapshot time, in the order they were taken


class Snapshot(Model):
    """The part of the DSS state ``S_t`` the IR needs: what each train occupies at ``time_s``."""

    network_version: str
    time_s: float
    trains: list[TrainState]
    departed: list[str] = []  # trains that entered and have left the section


def snapshot_from_occupancy(
    log: Sequence[tuple[float, str, str, bool]], time_s: float, network_version: str
) -> Snapshot:
    """Occupations at ``time_s`` from a chronological ``(time, resource, run id, occupied)`` log."""
    held: dict[str, dict[str, float]] = {}
    for t, label, run_id, occupied in log:
        if t > time_s:
            break
        if occupied:
            held.setdefault(run_id, {})[label] = t
        else:
            held.setdefault(run_id, {}).pop(label, None)
    return Snapshot(
        network_version=network_version,
        time_s=time_s,
        trains=[
            TrainState(
                run_id=run_id,
                occupations=[
                    Occupation(resource=r, since_s=t) for r, t in sorted(h.items(), key=lambda x: x[1])
                ],
            )
            for run_id, h in sorted(held.items())
            if h
        ],
        departed=sorted(run_id for run_id, h in held.items() if not h),
    )


class Window(Model):
    day: dt.date
    start_s: int
    end_s: int


class Instance(Model):
    format: str = FORMAT
    version: int = VERSION
    network_version: str
    scenario_id: str
    window: Window
    resources: list[Resource]
    trains: list[Train]
    objective: list[Component]

    @property
    def counts(self) -> dict[str, int]:
        ops = [o for t in self.trains for o in t.operations]
        return {
            "trains": len(self.trains),
            "resources": len(self.resources),
            "operations": len(ops),
            "edges": sum(len(o.successors) for o in ops),
            "objective_components": len(self.objective),
        }

    def to_json(self) -> str:
        return self.model_dump_json(indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Instance:
        return cls.model_validate_json(text)

    def to_displib(self) -> dict[str, Any]:
        """The instance as a DISPLIB 2025 document (``docs/twin/displib-crosscheck.md``).

        Each train gets a synthetic exit operation (no resources) after its last operations, so their
        resources are released as in this IR (a DISPLIB exit operation never releases them), and a
        synthetic entry operation when it has alternative first operations. Successors are plain
        indices in topological order; objective components are ``op_delay`` with integer constants.
        """
        if any(r.capacity != 1 for r in self.resources):
            raise ValueError("DISPLIB resources are exclusive; a resource has capacity > 1")
        trains: list[list[dict[str, Any]]] = []
        shifts: list[int] = []
        for t in self.trains:
            preds = {s.operation for o in t.operations for s in o.successors}
            roots = [o for o in t.operations if o.id not in preds]
            shift = int(len(roots) > 1)
            exit_index = shift + len(t.operations)
            ops: list[dict[str, Any]] = []
            if shift:
                ops.append(
                    {
                        "start_lb": min(o.start_lb for o in roots),
                        "start_ub": max(o.start_ub for o in roots),
                        "min_duration": 0,
                        "resources": [],
                        "successors": [shift + o.id for o in roots],
                    }
                )
            for o in t.operations:
                if any(e.delay or e.operation <= o.id for e in o.successors):
                    raise ValueError(f"{t.id} op {o.id}: DISPLIB successors carry no delay and point forward")
                ops.append(
                    {
                        "start_lb": o.start_lb,
                        "start_ub": o.start_ub,
                        "min_duration": o.min_duration,
                        "resources": [
                            {"resource": r.resource, "release_time": r.release_time} for r in o.resources
                        ],
                        "successors": [shift + e.operation for e in o.successors] or [exit_index],
                    }
                )
            ops.append({"min_duration": 0, "resources": [], "successors": []})
            trains.append(ops)
            shifts.append(shift)
        objective = []
        for c in self.objective:
            if not (float(c.coeff).is_integer() and float(c.increment).is_integer()):
                raise ValueError(f"DISPLIB objective constants are integers: {c}")
            objective.append(
                {
                    "type": "op_delay",
                    "train": c.train,
                    "operation": shifts[c.train] + c.operation,
                    "threshold": c.threshold,
                    "coeff": int(c.coeff),
                    "increment": int(c.increment),
                }
            )
        return {"trains": trains, "objective": objective}


def fl(x: float) -> int:
    """Round down (a relaxation), with a guard against float noise just below an integer."""
    return math.floor(x + 1e-9)


class _Builder:
    """Operations and edges of one train, before dead branches are pruned."""

    def __init__(self) -> None:
        self.ops: list[dict[str, Any]] = []
        self.edges: set[tuple[int, int]] = set()

    def add(self, **fields: Any) -> int:
        self.ops.append(fields)
        return len(self.ops) - 1

    def link(self, a: int, b: int) -> None:
        self.edges.add((a, b))

    def prune(self, first_visit: int, last_visit: int) -> set[int]:
        """Indices of operations on some path from the first visit to the last visit."""
        alive = set(range(len(self.ops)))
        changed = True
        while changed:
            changed = False
            for i in sorted(alive):
                has_in = any(b == i and a in alive for a, b in self.edges)
                has_out = any(a == i and b in alive for a, b in self.edges)
                v = self.ops[i]["visit"]
                kind = self.ops[i]["kind"]
                if (not has_in and not (kind == "station" and v == first_visit)) or (
                    not has_out and not (kind == "station" and v == last_visit)
                ):
                    alive.discard(i)
                    changed = True
        return alive


def _clearing_s(plan: Plan, register: Register) -> int:
    cfg = register.value("twin.sim")
    speed = float(cfg["clearing_speed_kmh"]) / KMH_PER_MPS
    adequate = float(register.value("block.adequate_distance_m.macls"))
    return fl((plan.template.length_m + adequate) / speed)


def _weight(plan: Plan, register: Register) -> float:
    weights = register.value("policy.weights")
    return float(weights.get(plan.category, weights[DEFAULT_WEIGHT_CLASS]))


def _station_lines(layout: Layout, plan: Plan, op: str) -> list[Any]:
    passenger = plan.kind == "passenger"
    return [
        sl
        for sl in layout.station_lines.get(op, [])
        if sl.kind in ("main", "loop")
        and sl.status == "in_use"
        and (sl.passenger_ok if passenger else sl.goods_ok)
    ]


def _block_duration(
    timing: Timing, plan: Plan, route: Sequence[Node], b_last: bool, min_dwell: float
) -> float:
    """Calibrated running time over ``route`` (block start, halts inside, block end), in seconds."""
    stopped = [n.stop for n in route]
    stopped[-1] = route[-1].stop or (b_last and plan.timetabled)
    total = 0.0
    for x, y, sx, sy in zip(route, route[1:], stopped, stopped[1:], strict=False):
        total += timing.leg(plan, x.op, y.op, sx, sy)
        if y is not route[-1] and y.stop:
            total += min_dwell
    return total


def _train(
    world: World, plan: Plan, horizon: int, resources: dict[str, Resource]
) -> tuple[Train, list[tuple[int, float, int]]]:
    layout, timing, reg = world.layout, world.timing, world.register
    cfg = reg.value("twin.sim")
    min_dwell, exit_clear = float(cfg["min_dwell_s"]), float(cfg["exit_clear_s"])
    clear = _clearing_s(plan, reg)
    idx = [i for i, n in enumerate(plan.nodes) if not layout.ops[n.op].is_halt]
    b = _Builder()
    prev_ends: list[tuple[int, BlockLine]] = []
    free_run = 0.0  # unimpeded time of the path, for the freight threshold
    for k, ni in enumerate(idx):
        node = plan.nodes[ni]
        last = k == len(idx) - 1
        stands = node.stop or last
        hop = None if last else (node.op, plan.nodes[idx[k + 1]].op)
        nxt = layout.block_by_pair[hop] if hop else None
        direction = layout.direction(*hop) if hop else ""
        duration = exit_clear if last else min_dwell if node.stop else 0.0
        lb = plan.entry_s if k == 0 else (node.arr if plan.timetabled and not node.stop else 0.0)
        station_ops: dict[int, int] = {}
        for sl in _station_lines(layout, plan, node.op):
            res = f"SL:{node.op}/{sl.line_no}"
            resources.setdefault(res, Resource(id=res, kind="station_line", capacity=sl.capacity_trains))
            i = b.add(
                visit=k,
                node=node.op,
                kind="station",
                min_duration=fl(duration),
                start_lb=fl(lb),
                start_ub=horizon,
                resources=[(res, 0 if last else clear)],
                block=None,
                line=None,
                stands=stands,
                line_no=sl.line_no,
            )
            station_ops[sl.line_no] = i
            for prev, line in prev_ends:
                if (line.block_id, line.line_id) in layout.receive.get((node.op, sl.line_no), set()):
                    b.link(prev, i)
        if hop is None or nxt is None:
            continue
        prev_ends = []
        route = [node, *plan.nodes[ni + 1 : idx[k + 1]], plan.nodes[idx[k + 1]]]
        total = _block_duration(timing, plan, route, idx[k + 1] == len(plan.nodes) - 1, min_dwell)
        free_run += total
        after_stop = node.dep if plan.timetabled and node.stop else 0.0
        for line in layout.lines_for(nxt.id, direction):
            subs = layout.canonical_sub_blocks(line.block_id, line.line_id) if line.system == "AUTO" else []
            if subs:
                length = sum(m for _, m in subs)
                parts = [(f"SB:{key}", "sub_block", total * m / length) for key, m in subs]
            else:
                parts = [(f"BL:{line.key}", "block", total)]
            chain: list[int] = []
            for n, (res, kind, span) in enumerate(parts):
                resources.setdefault(
                    res, Resource(id=res, kind="sub_block" if kind == "sub_block" else "block_line")
                )
                chain.append(
                    b.add(
                        visit=k,
                        node=node.op,
                        kind=kind,
                        min_duration=fl(span),
                        start_lb=fl(after_stop) if n == 0 else 0,
                        start_ub=horizon,
                        resources=[(res, clear)],
                        block=line.block_id,
                        line=line.line_id,
                    )
                )
                if n:
                    b.link(chain[n - 1], chain[n])
            for sl_no, i in station_ops.items():
                if (line.block_id, line.line_id) in layout.send.get((node.op, sl_no), set()):
                    b.link(i, chain[0])
            prev_ends.append((chain[-1], line))
    alive = sorted(b.prune(0, len(idx) - 1))
    new_id = {old: n for n, old in enumerate(alive)}
    succ: dict[int, list[int]] = defaultdict(list)
    for a, c in sorted(b.edges):
        if a in new_id and c in new_id:
            succ[new_id[a]].append(new_id[c])
    ops = [
        Operation(
            id=new_id[i],
            visit=b.ops[i]["visit"],
            node=b.ops[i]["node"],
            kind=b.ops[i]["kind"],
            min_duration=b.ops[i]["min_duration"],
            start_lb=b.ops[i]["start_lb"],
            start_ub=b.ops[i]["start_ub"],
            resources=[ResourceUse(resource=r, release_time=fl(rt)) for r, rt in b.ops[i]["resources"]],
            successors=[Successor(operation=s) for s in succ[new_id[i]]],
            block=b.ops[i]["block"],
            line=b.ops[i]["line"],
        )
        for i in alive
    ]
    # objective anchors: (operation id, threshold, kind marker) for station operations that count
    anchors: list[tuple[int, float, int]] = []
    visits = {k: plan.nodes[ni] for k, ni in enumerate(idx)}
    for o in ops:
        if o.kind != "station":
            continue
        node = visits[o.visit]
        last = o.visit == len(idx) - 1
        if plan.timetabled and (node.stop or last):
            anchors.append((o.id, node.arr, 0))
        elif not plan.timetabled and last:
            anchors.append((o.id, plan.entry_s + free_run, 0))
    return Train(id=plan.run_id, kind=plan.kind, category=plan.category, operations=ops), anchors


def _freeze(
    train: Train, anchors: list[tuple[int, float, int]], state: TrainState
) -> tuple[Train, list[tuple[int, float, int]]]:
    """Start ``train`` at the operations it occupies in the snapshot, pinned to when it took them."""
    ops = {o.id: o for o in train.operations}
    labels = [occ.resource for occ in state.occupations]
    chain: list[int] = []
    for first in (o for o in train.operations if o.resources[0].resource == labels[0]):
        chain = [first.id]
        for label in labels[1:]:
            nxt = [
                e.operation
                for e in ops[chain[-1]].successors
                if ops[e.operation].resources[0].resource == label
            ]
            if not nxt:
                break
            chain.append(nxt[0])
        if len(chain) == len(labels):
            break
    if len(chain) != len(labels):
        raise ValueError(f"{train.id}: occupations {labels} are not a path of its operations")
    succ = {o.id: [e.operation for e in o.successors] for o in train.operations}
    for a, c in itertools.pairwise(chain):
        succ[a] = [c]  # no alternative behind the head
    keep: set[int] = set()
    todo = [chain[0]]
    while todo:
        i = todo.pop()
        if i not in keep:
            keep.add(i)
            todo.extend(succ[i])
    new_id = {old: n for n, old in enumerate(sorted(keep))}
    since = {i: occ.since_s for i, occ in zip(chain, state.occupations, strict=True)}
    frozen_ops = []
    for old in sorted(keep):
        update: dict[str, Any] = {
            "id": new_id[old],
            "successors": [Successor(operation=new_id[e]) for e in succ[old]],
        }
        if old in since:
            update |= {"start_lb": fl(since[old]), "start_ub": math.ceil(since[old])}
        frozen_ops.append(ops[old].model_copy(update=update))
    kept = [(new_id[op], thr, k) for op, thr, k in anchors if op in new_id]
    return train.model_copy(update={"operations": frozen_ops}), kept


def build_instance(
    world: World,
    day: dt.date,
    start_s: float = 0.0,
    end_s: float | None = None,
    *,
    day_instance: str | None = None,
    plans: list[Plan] | None = None,
    snapshot: Snapshot | None = None,
) -> tuple[Instance, list[Plan]]:
    """Convert the trains entering in ``[start_s, end_s)`` of ``day``; returns the instance and the plans.

    With a ``snapshot`` taken at ``start_s`` (a rolling window), trains that entered earlier are
    converted too: those still in the section are frozen at their current operations, those that
    have not entered yet start free, and those that have left are dropped.
    """
    reg = world.register
    chosen = plans if plans is not None else plans_for(world, day, day_instance)[0]
    inst_id = day_instance or f"aug25-day-{day.day:02d}"
    end = float("inf") if end_s is None else end_s
    if snapshot is not None and abs(snapshot.time_s - start_s) > 1:
        raise ValueError(f"snapshot at {snapshot.time_s} s is not the window start {start_s} s")
    running = {st.run_id: st for st in snapshot.trains} if snapshot else {}
    departed = set(snapshot.departed) if snapshot else set()
    window_plans = [
        p
        for p in chosen
        if start_s <= p.entry_s < end
        or (snapshot is not None and p.entry_s < start_s and p.run_id not in departed)
    ]
    grace = float(reg.value("twin.sim")["horizon_grace_h"]) * SECONDS_PER_HOUR
    horizon = fl((end if end_s is not None else max(p.entry_s for p in chosen)) + grace)
    resources: dict[str, Resource] = {}
    trains: list[Train] = []
    objective: list[Component] = []
    for plan in window_plans:
        train, anchors = _train(world, plan, horizon, resources)
        if plan.run_id in running:
            train, anchors = _freeze(train, anchors, running[plan.run_id])
        weight = _weight(plan, reg)
        objective += [
            Component(train=len(trains), operation=op, coeff=weight, threshold=fl(thr))
            for op, thr, _ in anchors
        ]
        trains.append(train)
    instance = Instance(
        network_version=world.layout.network_version,
        scenario_id=f"{inst_id}-displib",
        window=Window(day=day, start_s=fl(start_s), end_s=horizon if end_s is None else fl(end_s)),
        resources=sorted(resources.values(), key=lambda r: r.id),
        trains=trains,
        objective=objective,
    )
    return instance, window_plans
