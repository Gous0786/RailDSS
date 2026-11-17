"""Greedy priority dispatcher (issue #75, spec FR-OPT-02/03/07/19/20).

Scenarios on the real day 20 Aug 2025 from 08:00: nominal, single-line working (a line closed for
maintenance) and a closed boundary exit; plus the rolling horizon and the end-to-end chain from the DSS's
detected conflicts to recommendations.
"""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace
from typing import Any

import pytest

from raildss_optimiser.greedy import GreedyDispatcher, GreedyPlan, RollingDispatcher
from raildss_twin import run
from raildss_twin.scenario import load_library
from raildss_twin.sim import Closure

DAY = dt.date(2025, 8, 20)
H = 3600.0
START = 8 * H
CLOSURE = Closure("DUSI~PDU", "UP", 8.5 * H, 9.5 * H)
EXIT = ("PSA", (8.5 * H, 9.0 * H))


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def dispatcher(world: run.World) -> GreedyDispatcher:
    return GreedyDispatcher(world)


@pytest.fixture(scope="module")
def scenarios(dispatcher: GreedyDispatcher) -> dict[str, GreedyPlan]:
    return {
        "nominal": dispatcher.plan(DAY, START, plan_id="nominal"),
        "single-line": dispatcher.plan(DAY, START, closures=[CLOSURE], plan_id="single-line"),
        "exit-closed": dispatcher.plan(DAY, START, exit_windows={EXIT[0]: [EXIT[1]]}, plan_id="exit-closed"),
    }


@pytest.mark.parametrize("name", ["nominal", "single-line", "exit-closed"])
def test_greedy_plan_passes_g2_and_moves_every_train(scenarios: dict[str, GreedyPlan], name: str) -> None:
    p = scenarios[name]
    fails = [(r.rule_id, r.violations[:1]) for r in p.g2.results if r.status.value in ("FAIL", "ERROR")]
    assert fails == [] and p.g2_fails == 0
    assert p.stuck == [] and p.ok
    assert p.decisions, "no departure decisions in the horizon"


def test_single_line_working_respects_the_closure(scenarios: dict[str, GreedyPlan]) -> None:
    p = scenarios["single-line"]
    mnt = next(r for r in p.g2.results if r.rule_id == "VER-MNT-01")
    assert mnt.status.value == "PASS"
    used = [
        (t.run_id, r.enter_s)
        for t in p.plan.trains
        for r in t.runs
        if set(r.block_id.split("-")) == {"DUSI", "PDU"} and r.line_id == "UP"
    ]
    assert all(not (CLOSURE.start_s - 600 < enter < CLOSURE.end_s) for _, enter in used)


def test_trains_are_held_before_a_closed_exit_and_never_exit_into_it(
    scenarios: dict[str, GreedyPlan],
) -> None:
    p = scenarios["exit-closed"]
    station, (a, b) = EXIT
    exits = [t.calls[-1] for t in p.plan.trains if t.calls[-1].op == station]
    assert exits and not [c for c in exits if a < c.arr_s < b]
    nominal = {(h.run_id, h.station) for h in scenarios["nominal"].holds}
    extra = [h for h in p.holds if (h.run_id, h.station) not in nominal]
    assert extra and all(h.station == "PUN" for h in extra)  # PUN: the last block station before PSA


def test_policy_serves_relief_then_higher_classes_first(dispatcher: GreedyDispatcher) -> None:
    def plan(run_id: str, kind: str, name: str = "") -> Any:
        return SimpleNamespace(run_id=run_id, kind=kind, name=name)

    service = {str(s.train_no): str(s.priority_class) for s in dispatcher.world.tables["service"]}  # type: ignore[attr-defined]
    by_class = {c: n for n, c in service.items()}
    plans = [
        plan(f"{by_class['premium']}@2025-08-20", "passenger"),
        plan(f"{by_class['mail_express']}@2025-08-20", "passenger"),
        plan(f"{by_class['passenger']}@2025-08-20", "passenger"),
        plan("LOAD1", "freight"),
        plan("LOAD2", "freight", "Relief train"),
    ]
    prio = dispatcher.priorities(plans)
    order = sorted(prio, key=prio.__getitem__)
    assert order == ["LOAD2", plans[0].run_id, plans[1].run_id, plans[2].run_id, "LOAD1"]


def test_rolling_horizon_replans_on_triggers_and_keeps_frozen_decisions(dispatcher: GreedyDispatcher) -> None:
    roll = RollingDispatcher(dispatcher, DAY)
    assert roll.step(START) is not None
    assert roll.step(START + 120, trigger="new_conflict") is not None  # a trigger re-plans at once
    assert roll.step(START + 240) is None  # no trigger, < 5 min since the last plan
    assert roll.step(START + 420) is not None  # 5 min since the last plan
    assert roll.max_gap_s <= roll.max_interval_s
    for prev, cur in zip(roll.plans, roll.plans[1:], strict=False):
        mine = {(d.run_id, d.station): d for d in cur.decisions}
        frozen = [d for d in prev.decisions if cur.start_s <= d.dep_s < cur.start_s + roll.freeze_s]
        for d in frozen:
            got = mine.get((d.run_id, d.station))
            assert got is not None and got.line_no == d.line_no and abs(got.dep_s - d.dep_s) <= 1, (d, got)
        frz = next(r for r in cur.g2.results if r.rule_id == "VER-FRZ-01")
        assert frz.status.value in ("PASS", "NOT_APPLICABLE")


def test_every_detected_conflict_gets_a_recommendation(world: run.World) -> None:
    from raildss_optimiser.pipeline import recommend_at

    out = recommend_at(world, DAY, START)
    assert out.conflicts, "no conflict detected at 08:00"
    assert len(out.recommendations) == len(out.conflicts)
    assert out.plan.ok
    for r in out.recommendations:
        assert r.status == "RESOLVED" and r.actions, r


V0 = [sc for sc in load_library() if sc.executable or all(d.type == "line_block" for d in sc.disruptions)]


@pytest.mark.parametrize("scenario", V0, ids=[sc.id for sc in V0])
def test_v0_scenarios_have_no_g2_fail_and_no_stuck_train(dispatcher: GreedyDispatcher, scenario: Any) -> None:
    starts = [d.start_s for d in scenario.disruptions]
    at = min(starts) - 1800 if starts else START  # half an hour before a line block, else 08:00
    p = dispatcher.plan_scenario(scenario, at)
    assert p.g2_fails == 0, [(r.rule_id, r.violations[:1]) for r in p.g2.results if r.status.value == "FAIL"]
    assert p.stuck == []
    if scenario.disruptions:
        mnt = next(r for r in p.g2.results if r.rule_id == "VER-MNT-01")
        assert mnt.status.value == "PASS"
