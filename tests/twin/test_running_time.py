"""Running-time calculator and calibration tests (issue #16; spec FR-TWIN-06, DSC-10)."""

from __future__ import annotations

import json

import pytest

from raildss_twin.calibration import CALIBRATION_FILE, CalibrationReport, run
from raildss_twin.rolling_stock import KMH_PER_MPS, RollingStock
from raildss_twin.running_time import Driving, Restriction, Track, simulate

STEP = 10.0


def _flat(vmax: float = 100.0, length_km: float = 10.0) -> Track:
    return Track(increasing=True, line_speed=[Restriction(0.0, length_km, vmax)])


@pytest.fixture(scope="module")
def stock() -> RollingStock:
    return RollingStock()


def test_cruise_at_line_speed_matches_distance_over_speed(stock: RollingStock) -> None:
    tpl = stock.passenger(22)
    run_ = simulate(_flat(100.0), tpl, 0.0, 10.0, False, False, Driving(), STEP)
    assert abs(run_.time_s - 10_000 / (100 / KMH_PER_MPS)) <= 1
    assert run_.max_kmh == 100.0


def test_stop_start_slower_than_pass_and_gradient_slows_heavy_train(stock: RollingStock) -> None:
    tpl = stock.freight("BOXN", True)
    through = simulate(_flat(75.0), tpl, 0.0, 10.0, False, False, Driving(), STEP).time_s
    stop_start = simulate(_flat(75.0), tpl, 0.0, 10.0, True, True, Driving(), STEP).time_s
    assert through < stop_start
    uphill = Track(True, [Restriction(0.0, 10.0, 75.0)], grade_breaks=[0.0], grade_permille=[8.0])
    downhill = Track(False, [Restriction(0.0, 10.0, 75.0)], grade_breaks=[0.0], grade_permille=[8.0])
    up = simulate(uphill, tpl, 0.0, 10.0, True, False, Driving(), STEP).time_s
    down = simulate(downhill, tpl, 10.0, 0.0, True, False, Driving(), STEP).time_s
    assert up > down


def test_psr_held_until_the_rear_clears(stock: RollingStock) -> None:
    tpl = stock.freight("BOXN", True)  # about 700 m long
    psr = Restriction(5.0, 5.1, 15.0)
    dn = Track(True, [Restriction(0.0, 10.0, 75.0)], restrictions=[psr])
    assert dn.limit_at(5.05, tpl.length_m) == 15.0
    assert dn.limit_at(5.5, tpl.length_m) == 15.0  # rear still on the PSR
    assert dn.limit_at(5.9, tpl.length_m) == 75.0
    up = Track(False, [Restriction(0.0, 10.0, 75.0)], restrictions=[psr])
    assert up.limit_at(4.5, tpl.length_m) == 15.0
    with_psr = simulate(dn, tpl, 0.0, 10.0, False, False, Driving(), STEP).time_s
    without = simulate(_flat(75.0), tpl, 0.0, 10.0, False, False, Driving(), STEP).time_s
    assert with_psr > without


def test_cruise_cap_and_reproducibility(stock: RollingStock) -> None:
    tpl = stock.passenger(22)
    capped = simulate(_flat(130.0), tpl, 0.0, 10.0, True, True, Driving(cruise_kmh=100.0), STEP)
    assert capped.max_kmh <= 100.0
    again = simulate(_flat(130.0), tpl, 0.0, 10.0, True, True, Driving(cruise_kmh=100.0), STEP)
    assert capped == again


@pytest.fixture(scope="module")
def report() -> CalibrationReport:
    return run()[1]


def test_freight_calibration_meets_dsc10(report: CalibrationReport) -> None:
    assert report.freight_blocks == 64
    assert report.freight_share >= 0.9
    assert report.freight_physics_within < report.freight_within  # supplements add, physics is reported


def test_pass_through_never_slower_and_reproducible(report: CalibrationReport) -> None:
    assert report.pass_slower_than_stop == []
    assert report.reproducible
    assert report.ok


def test_passenger_spans_reported_for_sme_review(report: CalibrationReport) -> None:
    # Approved definition: standard (modal) WTT time per stop pattern; padded spans reported only.
    assert report.spans_scored + report.spans_padded == report.spans_total
    assert 0 < report.spans_within <= report.spans_scored
    assert report.spans_within_all <= report.spans_total
    assert len(report.worst_blocks) == 10 and len(report.worst_spans) == 10
    assert report.passenger_target_met == (report.spans_share >= 0.95)


def test_committed_calibration_matches_a_fresh_run(report: CalibrationReport) -> None:
    committed = json.loads(CALIBRATION_FILE.read_text(encoding="utf-8"))["report"]
    assert committed == json.loads(report.model_dump_json())
