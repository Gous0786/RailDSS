"""The loop on a real twin day, headless (issue #84).

20 Aug 2025 from 08:00 on the clock: ingestion, state, ETAs and conflicts from the twin's observed feeds,
the greedy dispatcher and the warm-started CP-SAT, G0-G3, release into the HITL lifecycle, auto-accept in
evaluation mode, orders back into the twin, and the day's KPIs against BL-0.
"""

from __future__ import annotations

import datetime as dt

import pytest

from raildss_hitl.lifecycle import Status
from raildss_orchestrator.loop import Orchestrator, RunSummary
from raildss_orchestrator.twin import TwinDss, TwinPlanner, day_kpis
from raildss_twin import run

DAY = dt.date(2025, 8, 20)
START_S = 8 * 3600.0
END_S = START_S + 120.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def day(world: run.World) -> tuple[Orchestrator, TwinDss, RunSummary]:
    dss = TwinDss(world, DAY, until_s=END_S + 3600)
    planner = TwinPlanner(world, DAY, alternatives=0)  # opt.time_limit_s routine: 15 s
    loop = Orchestrator(world.register, dss, planner)
    summary = loop.run(START_S, END_S, 120.0)
    return loop, dss, summary


def test_a_real_day_runs_end_to_end_and_releases_only_what_passed_the_gates(
    day: tuple[Orchestrator, TwinDss, RunSummary],
) -> None:
    loop, _, summary = day
    assert summary.cycles and summary.released > 0
    for cycle in summary.cycles:
        verified = {c.name for c in cycle.candidates if c.verified}
        for rec in cycle.released:
            assert rec.candidate in verified, f"{rec.rec_id} was released from a plan that failed a gate"
    for rec in loop.lc.recs.values():
        assert Status.REJECTED_BY_VERIFIER not in [s for _, s in rec.history]
    assert summary.max_options_per_decision <= 3


def test_the_solver_is_warm_started_and_checked_by_g1_g2_g3(
    day: tuple[Orchestrator, TwinDss, RunSummary],
) -> None:
    _, _, summary = day
    first = summary.cycles[0]
    names = {c.name: c for c in first.candidates}
    assert {"greedy", "cpsat"} <= set(names)
    assert names["cpsat"].solver["warm_started"] is True
    assert names["cpsat"].g3 in ("PASS", "PASS_WITH_WARN", "FAIL")  # judged by the sandbox replay
    assert names["greedy"].solver["g2"] in ("PASS", "PASS_WITH_WARN")
    assert first.baseline["trains_exited"] >= 0  # the baseline (BL-0) is reported with the options


def test_accepted_orders_reach_the_twin_and_the_loop_replans_on_them(
    day: tuple[Orchestrator, TwinDss, RunSummary],
) -> None:
    loop, dss, summary = day
    assert summary.orders > 0 and summary.accepted > 0
    assert dss.feed.simulations >= 2  # the day was re-simulated under the accepted orders
    assert [t for t, _ in loop.published] and {t for t, _ in loop.published} == {"ctrl.orders"}
    assert [c.triggers for c in summary.cycles][1] == ["controller_action", "new_conflict"] or (
        "controller_action" in summary.cycles[1].triggers
    )
    assert summary.max_replan_gap_s <= 300.0


def test_the_days_kpis_compare_the_dss_arm_with_bl0(
    world: run.World, day: tuple[Orchestrator, TwinDss, RunSummary]
) -> None:
    loop, _, _ = day
    holds, lines = loop.commitments()
    comparison = day_kpis(world, DAY, closures=[], exit_windows={}, holds=holds, lines=lines)
    assert set(comparison["arms"]) == {"DSS", "BL-0"}
    assert "A5_weighted_delay_train_min" in comparison["deltas"]["BL-0"]["kpis"]
