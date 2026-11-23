"""Alert load on a nominal day (issue #163, DSC-09): the DSS chain over a window of SCN-01 and SCN-22."""

from __future__ import annotations

import pytest

from raildss_orchestrator import alert_load
from raildss_twin import run, scenario

WINDOW = (8 * 3600.0, 8 * 3600.0 + 1800.0)


@pytest.fixture(scope="module")
def library() -> dict[str, scenario.Scenario]:
    return {s.id: s for s in scenario.load_library_v1()}


def test_the_nominal_scenarios_are_real_days_without_disruptions(
    library: dict[str, scenario.Scenario],
) -> None:
    for sid in alert_load.NOMINAL:
        sc = library[sid]
        assert sc.freight_day is not None and sc.disruptions == [] and sc.executable


def test_alerts_of_a_nominal_window_are_counted_and_reported(library: dict[str, scenario.Scenario]) -> None:
    world = run.load_world()
    sc = library["SCN-01"]
    k = alert_load.run_day(world, sc, tick_s=60.0, from_s=WINDOW[0], to_s=WINDOW[1])
    assert k.hours == 0.5 and k.controllers == 1
    assert k.alerts == k.from_conflicts + k.from_data_quality
    assert k.per_controller_hour == round(k.alerts / 0.5, 2)
    assert k.flood_events >= 0 and k.max_in_flood_window <= k.alerts
    text = alert_load.render([(sc, k)], tick_s=60.0)
    assert "## SCN-01" in text and "H1 alerts per controller-hour" in text and "(DSC-09)" in text
