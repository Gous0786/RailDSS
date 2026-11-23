"""Alert load KPIs H1 and H2 on hand-made logs (issue #163, spec §12.4 DSC-09, FR-ALR-04)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from raildss_dss.alert_kpi import AlertLog
from raildss_orchestrator.report import alert_rows
from raildss_params import load

MIN = 60.0
HOUR = 3600.0


@dataclass
class C:
    name: str
    priority: str = "P2"

    @property
    def key(self) -> tuple[Any, ...]:
        return (1, ("T1", "T2"), "ALM~KUK", self.name)


def _log(ticks: list[tuple[float, list[C]]], dq: dict[float, list[dict[str, Any]]] | None = None) -> AlertLog:
    log = AlertLog(load())
    for now_s, conflicts in ticks:
        log.record(now_s, conflicts, (dq or {}).get(now_s, ()))
    return log


def test_the_thresholds_come_from_the_register() -> None:
    reg = load()
    log = AlertLog(reg)
    assert log.budget == reg.value("alert.steady_state_budget_per_h") == 6
    assert (log.flood_alerts, log.flood_window_s) == (10, 10 * MIN)
    assert log.controllers == reg.value("alert.controllers_on_duty")


def test_h1_is_the_alerts_per_controller_hour_of_the_window() -> None:
    log = _log([(i * 10 * MIN, [C(f"c{i}")]) for i in range(6)])  # a new conflict every 10 min
    k = log.kpis(0.0, 2 * HOUR)
    assert (k.alerts, k.hours, k.per_controller_hour) == (6, 2.0, 3.0)
    assert k.within_budget and k.flood_events == 0
    busy = _log([(i * 5 * MIN, [C(f"c{i}")]) for i in range(14)]).kpis(0.0, 1 * HOUR)
    assert busy.per_controller_hour == 14.0 and not busy.within_budget  # over the 6 per hour budget


def test_more_controllers_share_the_load_and_an_empty_window_has_no_rate() -> None:
    log = _log([(0.0, [C("a"), C("b"), C("c"), C("d")])])
    log.controllers = 2
    assert log.kpis(0.0, HOUR).per_controller_hour == 2.0
    assert _log([(0.0, [])]).kpis(0.0, HOUR).per_controller_hour == 0.0
    assert _log([(0.0, [])]).kpis(5.0, 5.0).per_controller_hour is None


def test_a_conflict_is_one_alert_while_it_is_reported_and_a_new_one_when_it_comes_back() -> None:
    a = C("a")
    log = _log([(0.0, [a]), (MIN, [a]), (2 * MIN, [a]), (3 * MIN, []), (4 * MIN, [a])])
    assert log.kpis(0.0, HOUR).alerts == 2


def test_only_priorities_p1_to_p3_count_and_data_quality_alerts_are_added() -> None:
    log = _log(
        [(0.0, [C("a", "P1"), C("b", "P3"), C("c", "P4")])],
        dq={0.0: [{"priority": "P2", "kind": "stale"}, {"priority": "P4", "kind": "info"}]},
    )
    k = log.kpis(0.0, HOUR)
    assert (k.alerts, k.from_conflicts, k.from_data_quality) == (3, 2, 1)
    assert k.by_priority == {"P1": 1, "P2": 1, "P3": 1}


def test_h2_a_flood_is_more_than_ten_alerts_in_ten_minutes() -> None:
    ten = _log([(i * 30.0, [C(f"c{i}")]) for i in range(10)])  # 10 alerts in 4.5 min
    assert ten.kpis(0.0, HOUR).flood_events == 0 and ten.kpis(0.0, HOUR).max_in_flood_window == 10
    eleven = _log([(i * 30.0, [C(f"c{i}")]) for i in range(11)])
    k = eleven.kpis(0.0, HOUR)
    assert (k.flood_events, k.max_in_flood_window) == (1, 11)
    spread = _log([(i * MIN, [C(f"c{i}")]) for i in range(20)])  # one a minute: 10 per 10 min at most
    assert spread.kpis(0.0, HOUR).flood_events == 0


def test_a_flood_that_goes_on_is_one_event_and_two_bursts_are_two() -> None:
    long_burst = _log([(i * 10.0, [C(f"c{i}")]) for i in range(40)])  # 40 alerts in 6.5 min
    assert long_burst.kpis(0.0, HOUR).flood_events == 1
    bursts = []
    for burst in range(2):
        start = burst * 30 * MIN
        bursts += [(start + i * 10.0, [C(f"b{burst}-{i}")]) for i in range(12)]
    log = _log(bursts)
    assert log.kpis(0.0, HOUR).flood_events == 2  # half an hour apart


def test_the_report_rows_show_both_kpis_against_the_targets() -> None:
    rows = "\n".join(alert_rows(_log([(0.0, [C("a")])]).kpis(0.0, HOUR).as_dict()))
    assert "H1 alerts per controller-hour | 1 (1 h, 1 controller) | <= 6 (DSC-09)" in rows
    assert "H2 flood events | 0" in rows and "| 0 (DSC-09) |" in rows
