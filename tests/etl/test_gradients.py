"""Gradients and curves tests (issue #14 acceptance criteria).

Most tests read the committed derived outputs, so CI needs no network. The re-derivation test runs
only where the OSM and DEM inputs are cached (``python -m raildss_etl geo-fetch``).
"""

from __future__ import annotations

import itertools
import json
import math

import numpy as np
import pandas as pd
import pytest

from raildss_etl import geo, gradients
from raildss_etl.gradients import GradientReport, Point, PsrCheck
from raildss_etl.load import run_stage1
from raildss_etl.topology import Topology, build_topology
from raildss_params import load
from raildss_schema.entities import Curve, Gradient


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    return run_stage1(write=False)[0]


@pytest.fixture(scope="module")
def topo(tables: dict[str, pd.DataFrame]) -> Topology:
    return build_topology(tables)[0]


@pytest.fixture(scope="module")
def grads() -> list[Gradient]:
    return gradients.read_gradients()


@pytest.fixture(scope="module")
def curves() -> list[Curve]:
    return gradients.read_curves()


@pytest.fixture(scope="module")
def report() -> GradientReport:
    text = (gradients.OUT_DIR / gradients.REPORT).read_text(encoding="utf-8")
    return GradientReport.model_validate(json.loads(text))


def test_every_block_has_a_synthetic_profile_within_the_bound(topo: Topology, grads: list[Gradient]) -> None:
    bound = float(load().value("gradient.derivation")["max_permille"])
    by_block: dict[str, list[Gradient]] = {}
    for g in grads:
        by_block.setdefault(g.block_section_id, []).append(g)
    km_of = {o.code: o.km for o in topo.operational_points}
    for block in topo.block_sections:
        segs = sorted(by_block[block.id], key=lambda g: g.from_km)
        lo, hi = sorted((km_of[block.from_op], km_of[block.to_op]))
        assert segs[0].from_km == pytest.approx(lo, abs=1e-3) and segs[-1].to_km == pytest.approx(
            hi, abs=1e-3
        )
        assert all(a.to_km == pytest.approx(b.from_km, abs=1e-3) for a, b in itertools.pairwise(segs))
    assert all(g.source == "synthetic" and abs(g.gradient_permille) <= bound for g in grads)
    assert len({g.id for g in grads}) == len(grads)


def test_report_is_ok_and_clipping_is_counted(report: GradientReport) -> None:
    assert report.ok and report.blocks_without_profile == []
    assert report.clipped == 2 and report.max_abs_permille < 11
    assert abs(report.alignment_m / 1000 - 176.83) < 1.5  # OSM alignment vs COA section length


def test_curve_at_km_810_811_is_found_within_one_km(report: GradientReport) -> None:
    checks = {c.psr_id: c for c in report.psr_checks}
    for psr in (5441, 29739):  # the KTV->PSA and PSA->KTV "3 degree curve" PSRs
        c = checks[psr]
        assert c.running_line_curve and c.confirmed
        assert c.distance_km is not None and c.distance_km <= report.psr_match_tolerance_km
    assert not checks[
        29740
    ].running_line_curve  # points and crossings in the KTV yard, reported as unconfirmed


def test_curves_are_synthetic_and_inside_their_blocks(topo: Topology, curves: list[Curve]) -> None:
    km_of = {o.code: o.km for o in topo.operational_points}
    blocks = {b.id: sorted((km_of[b.from_op], km_of[b.to_op])) for b in topo.block_sections}
    assert curves
    for c in curves:
        lo, hi = blocks[c.block_section_id]
        assert c.source == "synthetic" and lo <= c.from_km < c.to_km <= hi
        assert c.degree is not None and c.degree >= 1 and c.radius_m is not None and c.radius_m <= 1750


def test_float_predictor_round_trip() -> None:
    rng = np.random.default_rng(0)
    z = rng.uniform(0, 500, (4, 8)).astype(">f4")
    planes = z.view(np.uint8).reshape(4, 8, 4).transpose(0, 2, 1).reshape(4, 32)  # byte planes per row
    encoded = np.concatenate([planes[:, :1], np.diff(planes.astype(np.int16), axis=1) % 256], axis=1)
    assert np.array_equal(
        geo.decode_float_predictor(encoded.astype(np.uint8).tobytes(), 4, 8), z.astype(np.float32)
    )


def _points(xy: list[tuple[float, float]], step_m: float) -> list[Point]:
    m_per_deg, lat0, lon0 = 111_194.9, 18.0, 83.0
    return [
        Point(
            chain_m=i * step_m,
            km=i * step_m / 1000,
            km_hwh=0,
            lat=lat0 + y / m_per_deg,
            lon=lon0 + x / (m_per_deg * math.cos(math.radians(lat0))),
        )
        for i, (x, y) in enumerate(xy)
    ]


def test_radius_recovered_from_a_circle_and_a_straight() -> None:
    radius, step = 800.0, 25.0
    circle = [
        (radius * math.sin(i * step / radius), radius * (1 - math.cos(i * step / radius))) for i in range(40)
    ]
    r = gradients.radii(_points(circle, step), 100.0, step)
    assert r[20] == pytest.approx(radius, rel=0.02)
    straight = gradients.radii(_points([(i * step, 0.0) for i in range(40)], step), 100.0, step)
    assert math.isinf(straight[20])


def test_running_median_removes_a_spike_and_slope_sign() -> None:
    z = np.array([100.0, 100.0, 140.0, 100.0, 100.0])
    assert gradients.running_median(z, 3).tolist() == [100.0, 100.0, 100.0, 100.0, 100.0]
    km = np.array([0.0, 1.0, 2.0])
    assert gradients._slope_permille(km, np.array([10.0, 15.0, 20.0])) == pytest.approx(
        5.0
    )  # rising to larger km
    assert gradients._slope_permille(km, np.array([20.0, 15.0, 10.0])) == pytest.approx(-5.0)


def test_report_fails_without_a_confirmed_running_line_curve() -> None:
    def chk(confirmed: bool) -> PsrCheck:
        return PsrCheck(
            psr_id=1,
            block="A-B",
            km_hwh_from=810,
            km_hwh_to=812,
            reason="3 degree curve.",
            nearest_curve=None,
            distance_km=None,
            running_line_curve=True,
            confirmed=confirmed,
        )

    base = dict(
        alignment_m=1.0,
        points=1,
        anchors={},
        anchor_offsets_m={},
        gradient_records=1,
        clipped=0,
        max_abs_permille=0.0,
        curves=0,
        psr_match_tolerance_km=1.0,
        blocks_without_profile=[],
    )
    assert GradientReport(**base, psr_checks=[chk(True)]).ok
    assert not GradientReport(**base, psr_checks=[chk(False)]).ok
    assert not GradientReport(**base, psr_checks=[]).ok
    assert not GradientReport(**{**base, "blocks_without_profile": ["X-Y"]}, psr_checks=[chk(True)]).ok


def test_rederivation_matches_committed_outputs(
    tables: dict[str, pd.DataFrame], topo: Topology, grads: list[Gradient]
) -> None:
    try:
        paths = [geo.cached("osm_rail"), *(geo.cached(n) for n in gradients.DEM_INPUTS)]
    except KeyError:  # pragma: no cover
        pytest.skip("inputs not in the manifest")
    if not all(p.exists() for p in paths) or geo.fetch():
        pytest.skip("OSM/DEM inputs not cached (python -m raildss_etl geo-fetch)")
    result, rep = gradients.build(tables, topo, geo.load_osm(paths[0]), geo.Dem(paths[1:]))
    assert rep.ok
    assert [g.model_dump(mode="json") for g in result.gradients] == [g.model_dump(mode="json") for g in grads]


def test_segment_without_enough_points_is_reported_not_zero_filled(topo: Topology) -> None:
    # 5 km alignment step against 500 m segments: most segments hold no point at all
    points = [
        Point(chain_m=i * 5000.0, km=i * 5.0, km_hwh=0, lat=18.0, lon=83.0, elev_smooth_m=100.0 + i)
        for i in range(36)
    ]
    grads, _, _, missing = gradients.build_gradients(points, topo, 500.0, 25.0)
    assert missing and all("alignment point" in m for m in missing)
    assert not any(g.gradient_permille == 0 for g in grads)  # a row exists only where a slope was measured
    with pytest.raises(ValueError, match="alignment point"):
        gradients._slope_permille(np.array([1.0]), np.array([5.0]))
