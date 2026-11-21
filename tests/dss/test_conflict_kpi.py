"""Conflict KPIs D1 and D2 on hand-made logs (issue #162, spec §12.4 DSC-04, DSC-05)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from raildss_common.envelope import EventType
from raildss_dss.conflict_kpi import ConflictLog, gt_occurrence
from raildss_orchestrator.report import conflict_rows
from raildss_params import load

ORIGIN = dt.datetime(2025, 8, 20, tzinfo=dt.UTC)
MIN = 60.0


@dataclass
class C:
    """A conflict as the detector reports it."""

    name: str
    predicted_s: float
    trains: tuple[str, ...] = ("T1", "T2")
    resource: str = "ALM~KUK"
    type: int = 1
    near: bool = False

    @property
    def time(self) -> dt.datetime:
        return ORIGIN + dt.timedelta(seconds=self.predicted_s)

    @property
    def key(self) -> tuple[Any, ...]:
        return (self.type, self.trains, self.resource, self.name)


def _log(ticks: list[tuple[float, list[C]]]) -> ConflictLog:
    log = ConflictLog(ORIGIN)
    for now_s, conflicts in ticks:
        log.record(now_s, conflicts)
    return log


def test_d1_is_the_median_lead_time_from_first_detection_to_predicted_occurrence() -> None:
    a, b, c = C("a", 30 * MIN), C("b", 50 * MIN), C("c", 70 * MIN)
    log = _log([(10 * MIN, [a]), (20 * MIN, [a, b]), (30 * MIN, [a, b, c])])
    k = log.kpis()
    # leads at first detection: a 20 min, b 30 min, c 40 min (later ticks do not change them)
    assert k.conflicts == 3 and k.d1_median_lead_min == 30.0


def test_a_conflict_already_in_progress_has_lead_zero_and_no_conflicts_gives_no_d1() -> None:
    assert _log([(10 * MIN, [C("a", 5 * MIN)])]).kpis().d1_median_lead_min == 0.0
    empty = _log([(0.0, []), (MIN, [])]).kpis()
    assert empty.conflicts == 0 and empty.d1_median_lead_min is None and empty.d2_resolved_share_pct is None


def test_d2_a_conflict_that_goes_away_before_its_time_is_resolved() -> None:
    a = C("a", 10 * MIN)
    k = _log([(0.0, [a]), (MIN, [a]), (2 * MIN, [])]).kpis()
    assert (k.resolved, k.occurred, k.open, k.d2_resolved_share_pct) == (1, 0, 0, 100.0)


def test_d2_a_conflict_still_reported_when_it_comes_due_has_occurred() -> None:
    a = C("a", 10 * MIN)
    k = _log([(0.0, [a]), (5 * MIN, [a]), (11 * MIN, [a])]).kpis()
    assert (k.resolved, k.occurred, k.d2_resolved_share_pct) == (0, 1, 0.0)
    late = _log([(0.0, [a]), (11 * MIN, [a]), (12 * MIN, [])]).kpis()  # gone only after its time
    assert (late.resolved, late.occurred) == (0, 1)


def test_d2_leaves_conflicts_that_are_still_in_the_future_out_and_counts_them() -> None:
    a, b = C("a", 90 * MIN), C("b", 10 * MIN)
    k = _log([(0.0, [a, b]), (MIN, [a]), (2 * MIN, [a])]).kpis()
    assert (k.resolved, k.occurred, k.open) == (1, 0, 1) and k.d2_resolved_share_pct == 100.0
    only_open = _log([(0.0, [a]), (MIN, [a])]).kpis()
    assert only_open.open == 1 and only_open.d2_resolved_share_pct is None


def test_the_ground_truth_overrules_a_conflict_that_only_vanished_from_the_prediction() -> None:
    a, b = C("a", 10 * MIN), C("b", 10 * MIN, trains=("T3", "T4"))
    log = _log([(0.0, [a, b]), (MIN, [])])
    assert log.kpis().resolved == 2  # on the log alone both are resolved
    k = log.kpis(lambda t: t.trains == ("T1", "T2"))  # but T1 and T2 were held in the ground truth
    assert (k.resolved, k.occurred, k.d2_resolved_share_pct) == (1, 1, 50.0)


def test_a_conflict_that_comes_back_is_a_new_one_and_the_share_is_a_share() -> None:
    a = C("a", 10 * MIN)
    log = _log([(0.0, [a]), (MIN, []), (2 * MIN, [a]), (3 * MIN, [])])
    assert len(log.tracks) == 2 and log.kpis().resolved == 2
    nine = [C(f"r{i}", 30 * MIN) for i in range(9)]
    late = C("late", MIN)
    k = _log([(0.0, [*nine, late]), (2 * MIN, [late]), (3 * MIN, [late])]).kpis()
    assert (k.resolved, k.occurred, k.d2_resolved_share_pct) == (9, 1, 90.0)  # DSC-04 target


def test_near_conflicts_are_counted_and_stop_being_near_once_firm() -> None:
    n = C("n", 30 * MIN, near=True)
    firm = C("n", 30 * MIN, near=False)
    log = _log([(0.0, [n]), (MIN, [n])])
    assert log.kpis().near == 1
    log.record(2 * MIN, [firm])
    assert log.kpis().near == 0


def _gt(train: str, at_s: float, status: str, station: str) -> Any:
    return SimpleNamespace(
        event_type=EventType.TRAIN_STATE,
        train_run_id=train,
        event_time=ORIGIN + dt.timedelta(seconds=at_s),
        payload={"status": status, "station": station},
    )


def test_gt_occurrence_needs_a_held_train_of_the_conflict_at_its_resource_near_its_time() -> None:
    window = float(load().value("kpi.conflict_match_window_min")) * MIN
    t = _log([(0.0, [C("a", 600.0)])]).tracks[0]
    events = [
        _gt("T1", 600.0 + window - 1, "waiting", "KUK"),  # inside the window, a station of the segment
        _gt("T9", 600.0, "waiting", "KUK"),  # another train
        _gt("T2", 600.0, "running", "ALM"),  # not held
        _gt("T2", 600.0, "waiting", "CPP"),  # another place
        _gt("T2", 600.0 + window + 1, "waiting", "ALM"),  # outside the window
    ]
    assert gt_occurrence(events[:1], ORIGIN)(t)
    assert not gt_occurrence(events[1:], ORIGIN)(t)
    assert gt_occurrence(events, ORIGIN)(t)


def test_the_report_rows_show_both_kpis_against_their_targets() -> None:
    rows = "\n".join(conflict_rows(_log([(0.0, [C("a", 20 * MIN)]), (MIN, [])]).kpis().as_dict()))
    assert "D1 median conflict lead time | 20 min | >= 15 min (DSC-05)" in rows
    assert "D2 resolved before occurrence | 100 %" in rows and ">= 90 % (DSC-04)" in rows
    nothing = "\n".join(conflict_rows(_log([(0.0, [])]).kpis().as_dict()))
    assert "| - |" in nothing
