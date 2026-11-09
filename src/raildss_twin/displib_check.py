"""Independent evaluator over the IR (spec §9 G1; issue #22).

``evaluate`` checks a schedule against an :class:`~raildss_twin.displib.Instance` using only the IR
(not the solver that produced the schedule, and not the twin): each train follows a path from a
first operation to a last one along successor edges, start times respect the bounds and
``s_next >= s + min_duration + delay``, and no resource holds more trains than its capacity.
It also returns the objective value. ``schedule_from_trace`` turns the ground-truth occupancy log of
a twin run (BL-0) into such a schedule; ``serial_schedule`` builds a trivially feasible one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from raildss_twin.displib import Instance, Operation, Train

Schedule = dict[int, list[tuple[int, float]]]  # train index -> [(operation id, start time)] in path order


@dataclass
class Evaluation:
    violations: list[str] = field(default_factory=list)
    objective: float = 0.0

    @property
    def feasible(self) -> bool:
        return not self.violations


def _predecessors(train: Train) -> set[int]:
    return {s.operation for o in train.operations for s in o.successors}


def evaluate(instance: Instance, schedule: Schedule, tolerance_s: float = 1e-3) -> Evaluation:
    result = Evaluation()
    capacity = {r.id: r.capacity for r in instance.resources}
    intervals: dict[str, list[tuple[float, float, int]]] = {}
    started: dict[tuple[int, int], float] = {}
    for ti, train in enumerate(instance.trains):
        path = schedule.get(ti)
        if not path:
            result.violations.append(f"{train.id}: no schedule")
            continue
        ops = {o.id: o for o in train.operations}
        has_pred = _predecessors(train)
        first, last = ops[path[0][0]], ops[path[-1][0]]
        if first.id in has_pred:
            result.violations.append(
                f"{train.id}: path starts at operation {first.id}, which has a predecessor"
            )
        if last.successors:
            result.violations.append(f"{train.id}: path ends at operation {last.id}, which has successors")
        for n, (oid, s) in enumerate(path):
            op: Operation = ops[oid]
            started[(ti, oid)] = s
            if not op.start_lb - tolerance_s <= s <= op.start_ub + tolerance_s:
                result.violations.append(
                    f"{train.id} op {oid}: start {s:.0f} outside [{op.start_lb}, {op.start_ub}]"
                )
            if n + 1 < len(path):
                nid, ns = path[n + 1]
                edge = next((e for e in op.successors if e.operation == nid), None)
                if edge is None:
                    result.violations.append(f"{train.id}: no edge from operation {oid} to {nid}")
                elif ns + tolerance_s < s + op.min_duration + edge.delay:
                    earliest = s + op.min_duration + edge.delay
                    result.violations.append(
                        f"{train.id}: operation {nid} starts {ns:.0f}, before {earliest:.0f}"
                    )
                end = ns
            else:
                end = s + op.min_duration
            for use in op.resources:
                intervals.setdefault(use.resource, []).append((s, end + use.release_time, ti))
    for resource, spans in intervals.items():
        events = sorted(
            [(a, 1, t) for a, _, t in spans] + [(b, -1, t) for _, b, t in spans], key=lambda e: (e[0], e[1])
        )
        held: dict[int, int] = {}
        for time_s, kind, ti in events:
            if kind == -1:
                held[ti] = held.get(ti, 0) - 1
                if not held[ti]:
                    del held[ti]
            else:
                held[ti] = held.get(ti, 0) + 1
                others = [t for t in held if t != ti]
                if len(held) > capacity[resource] and others:
                    names = ", ".join(sorted(instance.trains[t].id for t in held))
                    result.violations.append(f"{resource} holds {names} at {time_s:.0f} s")
    for c in instance.objective:
        at = started.get((c.train, c.operation))
        if at is not None:
            result.objective += c.coeff * max(0.0, at - c.threshold) + (
                c.increment if at > c.threshold else 0.0
            )
    return result


def serial_schedule(instance: Instance) -> Schedule:
    """A trivially feasible schedule: trains one after another, each on its first path, no overlap."""
    schedule: Schedule = {}
    clock = 0.0
    for ti, train in enumerate(instance.trains):
        ops = {o.id: o for o in train.operations}
        preds = _predecessors(train)
        current = next(o for o in train.operations if o.id not in preds)
        s = max(clock, float(current.start_lb))
        path: list[tuple[int, float]] = []
        while True:
            path.append((current.id, s))
            if not current.successors:
                break
            edge = current.successors[0]
            nxt = ops[edge.operation]
            s = max(s + current.min_duration + edge.delay, float(nxt.start_lb))
            current = nxt
        release = max((u.release_time for o in train.operations for u in o.resources), default=0)
        clock = path[-1][1] + ops[path[-1][0]].min_duration + release
        schedule[ti] = path
    return schedule


def schedule_from_trace(
    instance: Instance, occupancy_log: Sequence[tuple[float, str, str, bool]]
) -> Schedule:
    """The BL-0 twin's choices as a schedule: acquisitions per train, matched to operations by resource.

    ``occupancy_log`` holds ``(time, resource label, train run id, occupied)`` as kept by the twin.
    """
    acquired: dict[str, list[tuple[str, float]]] = {}
    for time_s, label, run_id, occupied in occupancy_log:
        if occupied:
            acquired.setdefault(run_id, []).append((label, time_s))
    schedule: Schedule = {}
    for ti, train in enumerate(instance.trains):
        ops = {o.id: o for o in train.operations}
        preds = _predecessors(train)
        steps = acquired.get(train.id, [])
        # a train frozen at a window start begins at an operation it took before the start
        start, root = next(
            (n, o)
            for n, (label, time_s) in enumerate(steps)
            for o in train.operations
            if o.id not in preds and o.resources[0].resource == label and time_s >= o.start_lb
        )
        path = [(root.id, steps[start][1])]
        current = root
        for label, time_s in steps[start + 1 :]:
            current = next(
                ops[e.operation]
                for e in current.successors
                if ops[e.operation].resources[0].resource == label
            )
            path.append((current.id, time_s))
        schedule[ti] = path
    return schedule
