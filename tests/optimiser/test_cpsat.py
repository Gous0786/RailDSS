"""CP-SAT model of the solver IR (FR-OPT-01, FR-OPT-03; issue #76)."""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path
from typing import Any

from raildss_optimiser.cpsat import order_events, solve, stability_of
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


# ---------------------------------------------------------------- rolling horizon (issue #152)


def _path(result: Any, train: int) -> list[tuple[int, int]]:
    return [
        (int(e["operation"]), int(e["time"])) for e in result.solution["events"] if int(e["train"]) == train
    ]


def test_a_fixed_operation_is_used_and_ends_where_it_was_frozen() -> None:
    free = solve(PROBLEM, time_limit_s=10, workers=2)
    assert free.objective == 10
    held = solve(PROBLEM, time_limit_s=10, workers=2, fixed=[(0, 0, 8, 8)])  # A leaves l at 8, not 5
    assert held.solution is not None and g1(PROBLEM, held.solution).passed
    a = _path(held, 0)
    assert a[0][0] == 0 and a[1][1] == 8  # A's next operation starts when its frozen one ends
    assert held.objective == 13  # B waits for l until 8


def test_the_previous_plan_is_kept_by_the_stability_term() -> None:
    # the previous plan had A leave l at 7: a heavy stability term keeps it (B waits), a light one does not
    kept = solve(PROBLEM, time_limit_s=10, workers=2, preferred=[(0, 0, 7)], stability=(100, 1000))
    assert kept.solution is not None and g1(PROBLEM, kept.solution).passed
    assert _path(kept, 0)[1][1] == 7 and kept.stability == 0 and kept.objective == 12
    light = solve(PROBLEM, time_limit_s=10, workers=2, preferred=[(0, 0, 7)], stability=(0, 0))
    assert light.objective == 10
    assert stability_of({0: [(0, 0), (2, 5), (3, 12)]}, [(0, 2, 10), (0, 1, 10)], 1, 1000) == 2 + 1000


def test_the_delay_cap_bounds_the_objective() -> None:
    assert solve(PROBLEM, time_limit_s=10, workers=2, delay_cap=10).objective == 10
    capped = solve(PROBLEM, time_limit_s=10, workers=2, delay_cap=9)  # below the optimum
    assert capped.solution is None and capped.status == "INFEASIBLE"
