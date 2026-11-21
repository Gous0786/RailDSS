"""CP-SAT rolling horizon as the primary planner (issue #152; FR-OPT-07, DSC-07)."""

from __future__ import annotations

import datetime as dt

from raildss_optimiser.greedy import Decision
from raildss_orchestrator.loop import Orchestrator
from raildss_orchestrator.report import _planner_rows
from raildss_orchestrator.twin import TwinDss, TwinPlanner, _keeps, churn
from raildss_twin import run

DAY = dt.date(2025, 8, 20)
START_S = 8 * 3600.0


def test_churn_counts_changed_lines_and_moved_departures() -> None:
    prev = [Decision("A", "VZM", 1, 1000.0), Decision("B", "CHE", 2, 2000.0), Decision("C", "PSA", 3, 3000.0)]
    cur = [Decision("A", "VZM", 1, 1030.0), Decision("B", "CHE", 3, 2000.0), Decision("D", "KTV", 1, 10.0)]
    assert churn(prev, cur, 60.0) == 1  # B changed line; A moved 30 s; C and D are only in one plan
    assert churn(prev, [Decision("A", "VZM", 1, 1100.0)], 60.0) == 1


def test_a_hint_bounds_the_delay_only_when_it_keeps_the_frozen_decisions() -> None:
    hint = {
        "events": [
            {"time": 0, "train": 0, "operation": 0},
            {"time": 5, "train": 0, "operation": 2},
            {"time": 10, "train": 0, "operation": 3},
        ]
    }
    assert _keeps(hint, [(0, 0, 4, 6), (0, 2, 10, 10)])
    assert not _keeps(hint, [(0, 0, 6, 8)])  # ends at 5, frozen at 6-8
    assert not _keeps(hint, [(0, 1, 0, 99)])  # the hint does not use that operation


def test_cpsat_carries_the_rolling_plan_only_when_verified_and_never_worse_than_greedy() -> None:
    world = run.load_world()
    dss = TwinDss(world, DAY, until_s=START_S + 3600)
    loop = Orchestrator(world.register, dss, TwinPlanner(world, DAY, planner="cpsat", time_limit_s=10))
    summary = loop.run(START_S, START_S + 4 * 60, 120.0)
    assert summary.cycles
    for cy in summary.cycles:
        primary = [c for c in cy.candidates if c.solver.get("primary")]
        if cy.planner == "cpsat":  # the rolling state comes from a verified CP-SAT plan
            assert primary and primary[0].verified
        for c in primary:
            if c.solver.get("capped"):  # warm-started from a hint that keeps the freeze: never worse (DSC-07)
                assert c.solver["objective"] <= c.solver["hint_objective"]
        for c in cy.candidates:
            # greedy (fallback and hint) imposes the frozen decisions; a CP-SAT option that the twin replay
            # (G3) cannot hold to them is rejected, never released or carried
            if c.name == "greedy":
                assert "VER-FRZ-01" not in c.fail, (cy.now_s, c.name)
            if not c.verified:
                assert all(o.candidate != c.name for o in cy.released), (cy.now_s, c.name)
    rows = _planner_rows(summary)
    assert any("Rolling plan by CP-SAT" in r for r in rows) or not any(
        c.solver.get("primary") for cy in summary.cycles for c in cy.candidates
    )
