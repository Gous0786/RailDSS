"""OD-25 re-plan cadence and freeze, on a simulated clock (issue #84, FR-OPT-07)."""

from __future__ import annotations

import pytest

from raildss_optimiser.greedy import Decision
from raildss_orchestrator.cadence import Cadence
from raildss_params import load

MIN = 60.0


@pytest.fixture()
def cadence() -> Cadence:
    return Cadence(load())


def test_the_parameters_are_the_od_25_values(cadence: Cadence) -> None:
    assert cadence.max_interval_s == 5 * MIN
    assert cadence.freeze_s == 10 * MIN
    assert cadence.triggers == {"new_conflict", "disruption", "controller_action", "order_decline"}


def test_the_first_tick_plans_then_it_waits_for_the_period_or_a_trigger(cadence: Cadence) -> None:
    plans = []
    for tick in range(0, 31):  # a simulated clock, one tick a minute
        now = tick * MIN
        events = ["new_conflict"] if tick == 7 else ["noise"] if tick == 9 else []
        if cadence.due(now, events):
            cadence.mark(now)
            plans.append(tick)
    assert plans == [
        0,
        5,
        7,
        12,
        17,
        22,
        27,
    ]  # periodic every 5 min, plus the trigger at 7; "noise" is no trigger
    assert cadence.max_gap_s <= 5 * MIN


@pytest.mark.parametrize("event", ["new_conflict", "disruption", "controller_action", "order_decline"])
def test_every_od_25_trigger_replans_at_once(cadence: Cadence, event: str) -> None:
    cadence.mark(0.0)
    assert not cadence.due(30.0)
    assert cadence.due(30.0, [event])


def test_decisions_inside_the_freeze_window_are_frozen_until_they_start(cadence: Cadence) -> None:
    plan = [
        Decision("A", "S1", 1, 100.0),
        Decision("B", "S2", 2, 9 * MIN),  # inside the 10 min window from t = 0
        Decision("C", "S3", 1, 10 * MIN),  # the window is half open
        Decision("D", "S4", 1, 30 * MIN),
    ]
    kept = cadence.freeze(plan, 0.0)
    assert {d.run_id for d in kept} == {"A", "B"}
    # five minutes later the next plan changes, but A (started) is history and B stays frozen
    replanned = [Decision("B", "S2", 3, 12 * MIN), Decision("C", "S3", 1, 11 * MIN)]
    kept = cadence.freeze(replanned, 5 * MIN)
    by_run = {d.run_id: d for d in kept}
    assert set(by_run) == {"B", "C"}  # A has started; C enters the window
    assert by_run["B"].line_no == 2 and by_run["B"].dep_s == 9 * MIN  # the frozen decision is unchanged
    assert by_run["C"].dep_s == 11 * MIN
