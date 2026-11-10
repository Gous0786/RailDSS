"""Gates G0 and G1 (FR-VER-01, FR-VER-02; issue #77)."""

from __future__ import annotations

import ast
import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from raildss_verifier.gates import PlanBasis, StateSnapshot, g0, g1

ROOT = Path(__file__).resolve().parents[2]

# ------------------------------------------------------------------ G0

NOW = 50_000.0


def snapshot(**kw: Any) -> StateSnapshot:
    base: dict[str, Any] = {
        "snapshot_hash": "h1",
        "network_version": "KTV-PSA-2025-08",
        "taken_at_s": NOW - 30,
        "train_last_fix_s": {"12863": NOW - 20, "FRT-1": NOW - 60},
        "active_disruptions": [],
    }
    return StateSnapshot(**(base | kw))


def basis(**kw: Any) -> PlanBasis:
    base: dict[str, Any] = {
        "plan_id": "P1",
        "network_version": "KTV-PSA-2025-08",
        "snapshot_hash": "h1",
        "trains": ["12863", "FRT-1"],
    }
    return PlanBasis(**(base | kw))


def test_g0_passes_a_fresh_consistent_plan() -> None:
    assert g0(basis(), snapshot(), NOW).passed
    assert g0(basis(), snapshot(), NOW, stage="pre_execution").passed


@pytest.mark.parametrize(
    ("plan", "state", "stage", "check"),
    [
        (basis(), snapshot(taken_at_s=NOW - 600), "pre_solve", "snapshot_fresh"),
        (basis(network_version="OLD"), snapshot(), "pre_solve", "network_version"),
        (basis(snapshot_hash="h0"), snapshot(), "pre_execution", "snapshot_version"),
        (
            basis(),
            snapshot(train_last_fix_s={"12863": NOW - 20, "FRT-1": NOW - 900}),
            "pre_solve",
            "train_fresh",
        ),
        (basis(trains=["12863", "GHOST"]), snapshot(), "pre_solve", "train_known"),
        (basis(), snapshot(active_disruptions=["INC-0031"]), "pre_solve", "disruptions"),
    ],
    ids=["stale-snapshot", "network", "snapshot-changed", "stale-train", "unknown-train", "disruption"],
)
def test_g0_rejects_with_a_reason(plan: PlanBasis, state: StateSnapshot, stage: Any, check: str) -> None:
    result = g0(plan, state, NOW, stage=stage)
    assert result.status == "FAIL"
    assert check in {f.check for f in result.findings}


def test_g0_names_the_stale_train() -> None:
    result = g0(basis(), snapshot(train_last_fix_s={"12863": NOW - 20, "FRT-1": NOW - 900}), NOW)
    assert [f.trains for f in result.findings if f.check == "train_fresh"] == [["FRT-1"]]


# ------------------------------------------------------------------ G1: the DISPLIB 2025 §1.3 example

PROBLEM: dict[str, Any] = {
    "trains": [
        [  # train A: currently on l, continues on r1 or r2
            {"start_ub": 0, "min_duration": 5, "resources": [{"resource": "l"}], "successors": [1, 2]},
            {"min_duration": 5, "resources": [{"resource": "r1"}], "successors": [3]},
            {"min_duration": 5, "resources": [{"resource": "r2"}], "successors": [3]},
            {"min_duration": 0, "resources": [], "successors": []},
        ],
        [  # train B: currently on r1, continues on l
            {"start_ub": 0, "min_duration": 5, "resources": [{"resource": "r1"}], "successors": [1]},
            {"min_duration": 5, "resources": [{"resource": "l"}], "successors": [2]},
            {"min_duration": 0, "resources": [], "successors": []},
        ],
    ],
    "objective": [{"type": "op_delay", "train": 1, "operation": 2, "threshold": 0, "coeff": 1}],
}
SOLUTION: dict[str, Any] = {
    "objective_value": 10,
    "events": [
        {"time": 0, "train": 0, "operation": 0},
        {"time": 0, "train": 1, "operation": 0},
        {"time": 5, "train": 0, "operation": 2},  # A leaves l for r2 (r1 is held by B)
        {"time": 5, "train": 1, "operation": 1},  # B takes l at the same instant, after A's end event
        {"time": 10, "train": 0, "operation": 3},
        {"time": 10, "train": 1, "operation": 2},
    ],
}

Mutation = Callable[[dict[str, Any], dict[str, Any]], None]


def _event(s: dict[str, Any], i: int, **kw: Any) -> None:
    s["events"][i] |= kw


def _swap(s: dict[str, Any], i: int, j: int) -> None:
    s["events"][i], s["events"][j] = s["events"][j], s["events"][i]


def _exit_holds(p: dict[str, Any], s: dict[str, Any]) -> None:
    p["trains"][0][3]["resources"] = [{"resource": "z"}]
    p["trains"][1][2]["resources"] = [{"resource": "z"}]


MUTANTS: dict[str, tuple[str, Mutation]] = {
    "out-of-order": ("chronological", lambda p, s: _event(s, 2, time=6)),
    "skip-an-operation": ("path", lambda p, s: s["events"].pop(2)),
    "no-exit": ("path", lambda p, s: s["events"].pop(4)),
    "not-from-entry": ("path", lambda p, s: s["events"].pop(0)),
    "before-earliest-start": ("bounds", lambda p, s: p["trains"][0][2].update(start_lb=7)),
    "after-latest-start": ("bounds", lambda p, s: [_event(s, 0, time=1), _event(s, 1, time=1)]),
    "too-short": ("min_duration", lambda p, s: p["trains"][0][0].update(min_duration=6)),
    "takes-a-held-resource-first": ("resource", lambda p, s: _swap(s, 2, 3)),
    "inside-the-release-time": (
        "resource",
        lambda p, s: p["trains"][0][0]["resources"][0].update(release_time=2),
    ),
    "exit-never-releases": ("resource", _exit_holds),
    "wrong-objective": ("objective", lambda p, s: s.update(objective_value=9)),
    "unknown-operation": ("known_operation", lambda p, s: _event(s, 5, operation=7)),
    "train-missing": ("path", lambda p, s: s.update(events=[e for e in s["events"] if e["train"] == 0])),
}


def test_g1_accepts_the_feasible_solution_and_recomputes_its_objective() -> None:
    result = g1(PROBLEM, SOLUTION, names=["A", "B"])
    assert result.passed, result.findings
    assert result.objective == 10


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_g1_detects_each_injected_model_violation(name: str) -> None:
    check, mutate = MUTANTS[name]
    problem, solution = copy.deepcopy(PROBLEM), copy.deepcopy(SOLUTION)
    mutate(problem, solution)
    result = g1(problem, solution, names=["A", "B"])
    assert result.status == "FAIL"
    assert check in {f.check for f in result.findings}, result.findings


def test_g1_catch_rate_over_the_injected_set_is_100_percent() -> None:
    caught = 0
    for _, mutate in MUTANTS.values():
        problem, solution = copy.deepcopy(PROBLEM), copy.deepcopy(SOLUTION)
        mutate(problem, solution)
        caught += not g1(problem, solution).passed
    assert caught == len(MUTANTS)


def test_the_verifier_imports_nothing_from_the_optimiser_twin_or_etl() -> None:
    """The import-linter contract, checked from the sources too (NFR-MNT-02)."""
    forbidden = ("raildss_optimiser", "raildss_twin", "raildss_etl")
    hits = []
    for path in (ROOT / "src" / "raildss_verifier").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            hits += [f"{path.name}: {n}" for n in names if n.split(".")[0] in forbidden]
    assert hits == []
