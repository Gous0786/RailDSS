"""CP-SAT model of the solver IR (FR-OPT-01, FR-OPT-03; issue #76)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

from raildss_optimiser.cpsat import order_events, solve
from raildss_twin import displib, run
from raildss_verifier.gates import g1
from tests.verifier.test_gates import PROBLEM

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools"))
from cpsat_spike import bl0_solution

DAY = dt.date(2025, 8, 20)


def test_the_displib_example_is_solved_to_its_optimum_and_passes_g1() -> None:
    result = solve(PROBLEM, time_limit_s=10, workers=2)
    assert result.status == "OPTIMAL" and result.objective == 10
    assert result.solution is not None and g1(PROBLEM, result.solution).passed


def test_events_of_one_instant_put_the_releasing_train_first() -> None:
    paths = {0: [(0, 0), (2, 5), (3, 10)], 1: [(0, 0), (1, 5), (2, 10)]}
    events = order_events(PROBLEM, paths)
    at5 = [(e["train"], e["operation"]) for e in events if e["time"] == 5]
    assert at5 == [(0, 2), (1, 1)]  # A leaves l before B takes it


def test_a_real_window_hinted_with_bl0_is_solved_feasibly_and_no_worse(tmp_path: Path) -> None:
    world = run.load_world()
    instance, plans = displib.build_instance(world, DAY, 4 * 3600, 4 * 3600 + 1800)
    doc = instance.to_displib()
    _, sim = run.run_day(world, DAY, plans=plans)
    hint = bl0_solution(instance, doc, sim.occupancy_log)
    assert g1(doc, hint).passed
    result = solve(doc, time_limit_s=5, workers=2, hint=hint)
    assert result.solution is not None and g1(doc, result.solution).passed
    assert result.objective is not None and result.objective <= hint["objective_value"]
