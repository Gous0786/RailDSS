"""Gates G0 and G1 of the verification pipeline (spec §9.3; FR-VER-01, FR-VER-02; issue #77).

**G0 input / state sanity** (:func:`g0`) runs before solving and again before execution. It checks:

* the snapshot is fresh (``ver.g0.snapshot_max_age_min``);
* no train involved in the plan is stale (``state.stale_threshold_min`` since its last position fix);
* the plan was built on the current network version and, before execution, on the current snapshot;
* the active disruptions the plan assumed are the ones active now.

**G1 solver feasibility** (:func:`g1`) re-checks a solver's solution against the solver model, a
DISPLIB 2025 problem document (``Instance.to_displib()`` of the IR, #74). This is an evaluator written
here from the published DISPLIB semantics, independent of the solver and of the twin's evaluator.
It does not trust the solver's status. A solution is ``{"objective_value", "events": [{"time",
"train", "operation"}]}`` in global order. It is feasible when:

* events are in chronological order;
* each train's events form a path from its entry operation (index 0) to its exit (the last one)
  along successor edges;
* every start lies within ``[start_lb, start_ub]``;
* every operation lasts at least ``min_duration`` until the train's next event;
* when two trains use a resource, the earlier user's end event comes before the later user's start,
  and at least the resource's ``release_time`` earlier (an exit operation never releases its
  resources);
* the reported objective equals the recomputed one.

The verifier imports nothing from the optimiser, the twin or the ETL (NFR-MNT-02, import-linter).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from raildss_params import Register, load

SECONDS_PER_MIN = 60


class Finding(BaseModel):
    check: str
    detail: str
    trains: list[str] = Field(default_factory=list)


class GateResult(BaseModel):
    gate: Literal["G0", "G1"]
    status: Literal["PASS", "FAIL"]
    findings: list[Finding] = Field(default_factory=list)
    objective: float | None = None  # G1: the recomputed objective value

    @property
    def passed(self) -> bool:
        return self.status == "PASS"


def _result(gate: Literal["G0", "G1"], findings: list[Finding], objective: float | None = None) -> GateResult:
    return GateResult(
        gate=gate, status="FAIL" if findings else "PASS", findings=findings, objective=objective
    )


# ------------------------------------------------------------------ G0


class StateSnapshot(BaseModel):
    """What G0 needs to know about the DSS state ``S_t`` (FR-PRED-02)."""

    snapshot_hash: str
    network_version: str
    taken_at_s: float
    train_last_fix_s: dict[str, float] = Field(default_factory=dict)
    active_disruptions: list[str] = Field(default_factory=list)


class PlanBasis(BaseModel):
    """What a candidate plan was built on."""

    plan_id: str
    network_version: str
    snapshot_hash: str
    trains: list[str]
    assumed_disruptions: list[str] = Field(default_factory=list)


def g0(
    plan: PlanBasis,
    current: StateSnapshot,
    now_s: float,
    *,
    stage: Literal["pre_solve", "pre_execution"] = "pre_solve",
    register: Register | None = None,
) -> GateResult:
    """G0 for ``plan`` against the current snapshot at ``now_s``."""
    reg = register or load()
    max_age_s = float(reg.value("ver.g0.snapshot_max_age_min")) * SECONDS_PER_MIN
    stale_s = float(reg.value("state.stale_threshold_min")) * SECONDS_PER_MIN
    out: list[Finding] = []
    age = now_s - current.taken_at_s
    if age > max_age_s:
        out.append(
            Finding(check="snapshot_fresh", detail=f"snapshot is {age:.0f} s old (limit {max_age_s:.0f} s)")
        )
    if plan.network_version != current.network_version:
        out.append(
            Finding(
                check="network_version",
                detail=f"plan built on {plan.network_version}, network is {current.network_version}",
            )
        )
    if stage == "pre_execution" and plan.snapshot_hash != current.snapshot_hash:
        out.append(
            Finding(
                check="snapshot_version",
                detail=f"plan built on snapshot {plan.snapshot_hash}, state is now {current.snapshot_hash}",
            )
        )
    no_fix = sorted(t for t in plan.trains if t not in current.train_last_fix_s)
    stale = sorted(t for t in plan.trains if now_s - current.train_last_fix_s.get(t, now_s) > stale_s)
    if no_fix:
        out.append(
            Finding(check="train_known", detail="trains with no position in the snapshot", trains=no_fix)
        )
    if stale:
        out.append(
            Finding(
                check="train_fresh", detail=f"trains with no position fix for > {stale_s:.0f} s", trains=stale
            )
        )
    if sorted(plan.assumed_disruptions) != sorted(current.active_disruptions):
        out.append(
            Finding(
                check="disruptions",
                detail=f"plan assumed {sorted(plan.assumed_disruptions)}, "
                f"active {sorted(current.active_disruptions)}",
            )
        )
    return _result("G0", out)


# ------------------------------------------------------------------ G1


def _ops(problem: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    return list(problem["trains"])


def objective_value(problem: Mapping[str, Any], starts: Mapping[tuple[int, int], float]) -> float:
    """DISPLIB ``op_delay``: ``coeff * max(0, t - threshold) + increment * [t > threshold]``."""
    total = 0.0
    for c in problem.get("objective", []):
        t = starts.get((int(c["train"]), int(c["operation"])))
        if t is None:
            continue
        threshold = float(c.get("threshold", 0))
        total += float(c.get("coeff", 0)) * max(0.0, t - threshold)
        total += float(c.get("increment", 0)) * (1.0 if t > threshold else 0.0)
    return total


def g1(
    problem: Mapping[str, Any],
    solution: Mapping[str, Any],
    *,
    names: Sequence[str] | None = None,
    register: Register | None = None,
) -> GateResult:
    """G1: is ``solution`` feasible for the DISPLIB ``problem``, with the objective it claims?"""
    reg = register or load()
    tol = float(reg.value("ver.g1.objective_tolerance"))
    trains = _ops(problem)
    label = list(names) if names is not None else [str(i) for i in range(len(trains))]
    out: list[Finding] = []
    events: list[Mapping[str, Any]] = list(solution.get("events", []))

    def bad(check: str, detail: str, *tr: int) -> None:
        out.append(Finding(check=check, detail=detail, trains=[label[i] for i in tr if 0 <= i < len(label)]))

    # chronological order and known operations
    for k in range(1, len(events)):
        if float(events[k]["time"]) < float(events[k - 1]["time"]):
            bad(
                "chronological",
                f"event {k} at {events[k]['time']} precedes event {k - 1}",
                int(events[k]["train"]),
            )
    per_train: dict[int, list[tuple[int, int, float]]] = {}  # train -> [(global index, op, time)]
    for k, e in enumerate(events):
        ti, oi = int(e["train"]), int(e["operation"])
        if not (0 <= ti < len(trains) and 0 <= oi < len(trains[ti])):
            bad("known_operation", f"event {k} names train {ti} operation {oi}, which does not exist", ti)
            continue
        per_train.setdefault(ti, []).append((k, oi, float(e["time"])))

    starts: dict[tuple[int, int], float] = {}
    ends: dict[tuple[int, int], tuple[int, float] | None] = {}  # (train, op) -> (global index, time) of end
    for ti, ops in enumerate(trains):
        seq = per_train.get(ti, [])
        if not seq:
            bad("path", f"train {label[ti]} has no events", ti)
            continue
        if seq[0][1] != 0:
            bad("path", f"train {label[ti]} starts at operation {seq[0][1]}, not its entry 0", ti)
        if seq[-1][1] != len(ops) - 1:
            bad("path", f"train {label[ti]} ends at operation {seq[-1][1]}, not its exit {len(ops) - 1}", ti)
        for n, (_, oi, t) in enumerate(seq):
            op = ops[oi]
            starts[(ti, oi)] = t
            lb, ub = float(op.get("start_lb", 0)), float(op.get("start_ub", float("inf")))
            if not lb <= t <= ub:
                bad("bounds", f"train {label[ti]} op {oi} starts at {t:g}, outside [{lb:g}, {ub:g}]", ti)
            if n + 1 < len(seq):
                k2, o2, t2 = seq[n + 1]
                if o2 not in op.get("successors", []):
                    bad("path", f"train {label[ti]}: op {o2} is not a successor of op {oi}", ti)
                if t2 - t < float(op["min_duration"]):
                    bad(
                        "min_duration",
                        f"train {label[ti]} op {oi} lasts {t2 - t:g} < {op['min_duration']}",
                        ti,
                    )
                ends[(ti, oi)] = (k2, t2)
            else:
                ends[(ti, oi)] = None  # the exit operation never ends

    # resource exclusivity with release times and event order
    users: dict[
        str, list[tuple[int, int, int, float, float]]
    ] = {}  # res -> (start index, train, op, start, release)
    for ti, seq in per_train.items():
        if not 0 <= ti < len(trains):
            continue
        for k, oi, t in seq:
            for use in trains[ti][oi].get("resources", []):
                users.setdefault(str(use["resource"]), []).append(
                    (k, ti, oi, t, float(use.get("release_time", 0)))
                )
    for res, uses in users.items():
        uses.sort()
        for a, (_, ta, oa, _, rel) in enumerate(uses):
            for kb, tb, ob, sb, _ in uses[a + 1 :]:
                if tb == ta:
                    continue
                end = ends.get((ta, oa))
                if end is None:
                    bad(
                        "resource",
                        f"{res}: {label[ta]} op {oa} never releases it (exit), {label[tb]} op {ob} uses it",
                        ta,
                        tb,
                    )
                    continue
                ke, te = end
                if ke > kb:
                    bad(
                        "resource",
                        f"{res}: {label[tb]} op {ob} starts before {label[ta]} op {oa} has ended",
                        ta,
                        tb,
                    )
                elif sb < te + rel:
                    bad(
                        "resource",
                        f"{res}: {label[tb]} op {ob} starts {te + rel - sb:g} before the release of "
                        f"{label[ta]} op {oa}",
                        ta,
                        tb,
                    )

    value = objective_value(problem, starts)
    if "objective_value" in solution and abs(float(solution["objective_value"]) - value) > tol:
        bad("objective", f"reported objective {solution['objective_value']} but recomputed {value:g}")
    return _result("G1", out, value)
