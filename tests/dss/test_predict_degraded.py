"""BL-3: the degraded predictor (issue #160, spec §12.2, FR-SCN-08).

The live part is the 4 h twin window of the prediction tests (20 Aug 2025, D0) through DS-1 and DS-3.
"""

from __future__ import annotations

import datetime as dt
import statistics
from typing import Any

import pytest

from raildss_dss.ingest import Ingestor, MemoryTransport
from raildss_dss.predict import DegradedPredictor, Predictor, ProcessTimes
from raildss_dss.state import PlanDirectory, StateEstimator
from raildss_orchestrator.twin import TwinDss
from raildss_twin import run
from raildss_twin.observe import Observer
from raildss_twin.plans import day_start

DAY = dt.date(2025, 8, 20)
HOUR = 3600.0
NOW = day_start(DAY) + dt.timedelta(hours=3)
SECONDS_PER_MINUTE = 60.0


@pytest.fixture(scope="module")
def setup() -> dict[str, Any]:
    world = run.load_world()
    reg = world.register
    directory = PlanDirectory.from_published(register=reg)
    process = ProcessTimes.from_log([], directory, reg)
    obs = Observer(reg, "D0", keep=True)
    run.run_day(world, DAY, until_s=4 * HOUR, observers=[obs])
    ing = Ingestor(reg)
    ing.run(MemoryTransport([(t, e.to_json()) for t, e in obs.events]))
    est = StateEstimator(directory, reg)
    for e in sorted(ing.released, key=lambda e: e.event_time):
        if e.event_time <= NOW:
            est.apply(e)
    base = Predictor(process, reg).predict(est, NOW)
    return {"reg": reg, "process": process, "est": est, "base": base, "world": world}


def _degraded(setup: dict[str, Any], seed: int = 0) -> DegradedPredictor:
    return DegradedPredictor(setup["process"], setup["reg"], seed)


def _key(e: Any) -> tuple[str, str, str]:
    return (e.run_id, e.station, e.kind)


def test_the_noise_model_comes_from_the_register(setup: dict[str, Any]) -> None:
    model = setup["reg"].value("pred.degraded")
    p = _degraded(setup)
    assert p.bias_s == model["bias_min"] * SECONDS_PER_MINUTE
    assert p.spread_frac == model["spread_frac"]


def test_same_seed_same_etas_other_seed_other_etas(setup: dict[str, Any]) -> None:
    a = _degraded(setup, 1).predict(setup["est"], NOW)
    assert a == _degraded(setup, 1).predict(setup["est"], NOW)
    assert a != _degraded(setup, 2).predict(setup["est"], NOW)


def test_only_the_etas_differ_from_the_normal_predictor(setup: dict[str, Any]) -> None:
    base, noisy = setup["base"], _degraded(setup).predict(setup["est"], NOW)
    assert [_key(e) for e in noisy] == [_key(e) for e in base]
    assert all(n.booked == b.booked and n.stale == b.stale for n, b in zip(noisy, base, strict=True))
    assert any(n.eta_p50 != b.eta_p50 for n, b in zip(noisy, base, strict=True))
    for n in noisy:
        assert NOW <= n.eta_p50 <= n.eta_p90  # nothing predicted in the past; P90 never below P50


def test_with_no_noise_it_is_the_normal_predictor(setup: dict[str, Any]) -> None:
    p = _degraded(setup)
    p.bias_s, p.spread_frac = 0.0, 0.0
    assert p.predict(setup["est"], NOW) == setup["base"]


def test_the_bias_and_a_spread_that_grows_with_the_lead_time(setup: dict[str, Any]) -> None:
    p = _degraded(setup)
    base = {_key(e): e for e in setup["base"]}
    shifts = [
        ((n.eta_p50 - NOW).total_seconds(), (n.eta_p50 - base[_key(n)].eta_p50).total_seconds())
        for n in p.predict(setup["est"], NOW)
        if (base[_key(n)].eta_p50 - NOW).total_seconds() > 0  # not clamped at t
    ]
    near = [d for lead, d in shifts if lead < 0.5 * HOUR]
    far = [d for lead, d in shifts if lead > 1.0 * HOUR]
    assert len(near) >= 10 and len(far) >= 10
    assert statistics.pstdev(far) > 2 * statistics.pstdev(near)
    assert abs(statistics.fmean([d for _, d in shifts]) - p.bias_s) < p.bias_s + p.spread_frac * HOUR


def test_an_event_keeps_its_error_from_one_cycle_to_the_next(setup: dict[str, Any]) -> None:
    """The draw is fixed per event; only its scale follows the lead time."""
    p = _degraded(setup)
    e = setup["base"][0]
    first, again = p._shift_s(e, HOUR), p._shift_s(e, 2 * HOUR)
    assert again - p.bias_s == pytest.approx(2 * (first - p.bias_s))
    assert p._shift_s(e, HOUR) == first


def test_the_bl3_arm_differs_from_the_normal_arm_only_through_the_predictor(setup: dict[str, Any]) -> None:
    world = setup["world"]
    normal = TwinDss(world, DAY, until_s=3 * HOUR + 600)
    bl3 = TwinDss(world, DAY, until_s=3 * HOUR + 600, degraded_predictor=True, noise_seed=3)
    assert type(normal.predictor) is Predictor and isinstance(bl3.predictor, DegradedPredictor)
    assert bl3.predictor.seed == 3
    a, b = normal.advance(3 * HOUR), bl3.advance(3 * HOUR)
    assert a.state_hash == b.state_hash and a.version == b.version  # the estimated state is the same
