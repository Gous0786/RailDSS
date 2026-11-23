"""Direction gates and unavailable resources in the CP-SAT model and in G1 (issue #153)."""

from __future__ import annotations

from typing import Any

from raildss_optimiser import cpsat
from raildss_params import load
from raildss_verifier import gates

REGISTER = load()


def _train(start: int, subs: list[str], direction: str, gate: str | None = "MID") -> list[dict[str, Any]]:
    """Entry, one operation per sub-block (60 s each) on a gated line, exit."""
    n = len(subs)
    ops: list[dict[str, Any]] = [
        {"start_lb": start, "start_ub": 3600, "min_duration": 0, "resources": [], "successors": [1]}
    ]
    for k, res in enumerate(subs):
        op: dict[str, Any] = {
            "start_lb": 0,
            "min_duration": 60,
            "resources": [{"resource": res, "release_time": 10}],  # clearing time, as in the IR
            "successors": [k + 2],
        }
        if gate:
            op["gate"] = [gate, direction]
        ops.append(op)
    ops.append({"min_duration": 0, "resources": [], "successors": []})
    assert len(ops) == n + 2
    return ops


def _doc(gate: str | None = "MID") -> dict[str, Any]:
    # two trains of opposite directions on one reversible line, on different sub-blocks of it (A, B and C),
    # both wanting to run at t = 0: only the direction of traffic keeps them apart
    trains = [_train(0, ["A", "B"], "DN", gate), _train(0, ["C"], "UP", gate)]
    return {
        "trains": trains,
        "objective": [
            {
                "type": "op_delay",
                "train": t,
                "operation": len(ops) - 1,
                "threshold": 0,
                "coeff": 1,
                "increment": 0,
            }
            for t, ops in enumerate(trains)
        ],
    }


def _runs(solution: dict[str, Any]) -> dict[int, tuple[int, int]]:
    paths = cpsat.paths_from_solution({}, solution)
    return {t: (p[1][1], p[-1][1]) for t, p in paths.items()}  # first sub-block start, exit


def test_opposing_trains_do_not_share_a_reversible_line() -> None:
    res = cpsat.solve(_doc(), time_limit_s=10, workers=1)
    assert res.solution is not None and res.status == "OPTIMAL"
    (a0, a1), (b0, b1) = _runs(res.solution).values()
    assert a1 <= b0 or b1 <= a0  # one direction at a time
    assert gates.g1(_doc(), res.solution, register=REGISTER).passed
    free = cpsat.solve(_doc(gate=None), time_limit_s=10, workers=1)  # without the gate they would cross
    assert free.objective is not None and res.objective is not None and free.objective < res.objective


def test_g1_finds_opposing_trains_on_a_gate_and_a_blocked_resource() -> None:
    doc = _doc(gate=None)
    loose = cpsat.solve(doc, time_limit_s=10, workers=1)
    assert loose.solution is not None
    gated = _doc()
    findings = gates.g1(gated, loose.solution, register=REGISTER).findings
    assert any(f.check == "direction" for f in findings)
    blocked = doc | {"unavailable": [{"resource": "C", "start": 0, "end": 1000}]}
    assert any(
        f.check == "unavailable" for f in gates.g1(blocked, loose.solution, register=REGISTER).findings
    )


def test_an_unavailable_resource_is_left_alone_over_its_window() -> None:
    doc = _doc(gate=None) | {"unavailable": [{"resource": "B", "start": 0, "end": 600}]}
    res = cpsat.solve(doc, time_limit_s=10, workers=1)
    assert res.solution is not None
    paths = cpsat.paths_from_solution(doc, res.solution)
    assert paths[0][2][1] >= 600  # the DN train enters sub-block B once it is back in use
    assert gates.g1(doc, res.solution, register=REGISTER).passed
