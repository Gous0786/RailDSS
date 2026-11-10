"""Twin perturbations (spec FR-TWIN-11; issue #56)."""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

import pytest

from raildss_twin import run
from raildss_twin.perturb import CAUSE_ENTRY, Perturbations

DAY = dt.date(2025, 8, 20)
SEEDS = range(30)
WITHIN_15_MIN_S = 900.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def pert(world: run.World) -> Any:
    return lambda seed: Perturbations(world.register, seed)


@pytest.fixture(scope="module")
def passengers(world: run.World) -> list[Any]:
    plans = run.plans_for(world, DAY)[0]
    return [p for p in plans if p.kind == "passenger" and p.timetabled]


def _entry_delays(pert: Any, plans: list[Any]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for seed in SEEDS:
        for p in plans:
            out[p.category].append(pert(seed).entry_delay_s(p.kind, p.category, f"{seed}/{p.run_id}"))
    return out


def test_entry_delay_shares_fall_inside_the_ecor_band_over_30_seeded_days(
    world: run.World, pert: Any, passengers: list[Any]
) -> None:
    cfg = world.register.value("delay.passenger.entry_profile")
    band = cfg["target_on_time_15min_share"]
    delays = _entry_delays(pert, passengers)
    pooled = [d for ds in delays.values() for d in ds]
    share = sum(d <= WITHIN_15_MIN_S for d in pooled) / len(pooled)
    assert band["low"] <= share <= band["high"], share
    # each category near the share its mixture implies (0.79 / 0.72 / 0.66)
    mu, sigma = cfg["tail_lognormal"]["mu_ln_min"], cfg["tail_lognormal"]["sigma"]
    tail = 0.5 * (1 + math.erf((math.log(15) - mu) / (sigma * math.sqrt(2))))
    for cat, ds in delays.items():
        p = cfg["p_on_time"][cat]
        implied = p + (1 - p) * tail
        got = sum(d <= WITHIN_15_MIN_S for d in ds) / len(ds)
        assert abs(got - implied) < 4 * math.sqrt(implied * (1 - implied) / len(ds)), (cat, got, implied)


def _mixture_cdf(minutes: float, p_on: float, window: tuple[float, float], mu: float, sigma: float) -> float:
    lo, hi = window
    on = min(max((minutes - lo) / (hi - lo), 0.0), 1.0)
    tail = 0.5 * (1 + math.erf((math.log(minutes) - mu) / (sigma * math.sqrt(2)))) if minutes > 0 else 0.0
    return p_on * on + (1 - p_on) * tail


def _ks_p(sample: list[float], cdf: Any) -> float:
    xs = sorted(sample)
    n = len(xs)
    d = max(max((i + 1) / n - cdf(x), cdf(x) - i / n) for i, x in enumerate(xs))
    z = math.sqrt(n) * d
    return min(1.0, 2 * sum((-1) ** (k - 1) * math.exp(-2 * k * k * z * z) for k in range(1, 101)))


def test_generated_entry_delays_match_the_configured_mixture_ks_over_30_seeds(
    world: run.World, pert: Any, passengers: list[Any]
) -> None:
    cfg = world.register.value("delay.passenger.entry_profile")
    window = (cfg["on_time_window_min"]["low"], cfg["on_time_window_min"]["high"])
    mu, sigma = cfg["tail_lognormal"]["mu_ln_min"], cfg["tail_lognormal"]["sigma"]
    for cat, ds in _entry_delays(pert, passengers).items():
        p_on = cfg["p_on_time"][cat]
        p_value = _ks_p([d / 60 for d in ds], lambda x, p=p_on: _mixture_cdf(x, p, window, mu, sigma))
        assert p_value > 0.05, (cat, p_value)


def test_freight_is_not_delayed_at_entry_and_dwell_is_never_shortened(pert: Any) -> None:
    pt = pert(3)
    assert pt.entry_delay_s("freight", "freight", "x") == 0.0
    assert all(pt.dwell_s(100.0, f"t{i}", "KPL") >= 100.0 for i in range(200))
    factors = [pt.leg_factor("freight", f"t{i}", "A", "B") for i in range(2000)]
    assert abs(sum(factors) / len(factors) - 1.0) < 0.01
    assert min(factors) >= 0.5


def test_draws_do_not_depend_on_the_order_they_are_asked_for(pert: Any) -> None:
    a = [pert(1).leg_factor("passenger", "t", "A", "B"), pert(1).leg_factor("passenger", "t", "B", "C")]
    b = [pert(1).leg_factor("passenger", "t", "B", "C"), pert(1).leg_factor("passenger", "t", "A", "B")]
    assert a == b[::-1]


def test_same_seed_gives_the_same_trace_hash_and_a_different_seed_a_different_one(world: run.World) -> None:
    a, _ = run.run_day(world, DAY, seed=1, until_s=4 * 3600, perturb=True)
    b, _ = run.run_day(world, DAY, seed=1, until_s=4 * 3600, perturb=True)
    c, _ = run.run_day(world, DAY, seed=2, until_s=4 * 3600, perturb=True)
    base, _ = run.run_day(world, DAY, seed=1, until_s=4 * 3600)
    assert a.trace_hash == b.trace_hash
    assert len({a.trace_hash, c.trace_hash, base.trace_hash}) == 3


def test_a_perturbed_day_completes_and_primary_entry_delays_carry_a_cause_code(world: run.World) -> None:
    report, sim = run.run_day(world, DAY, seed=5, perturb=True, keep_events=True)
    assert report.completed and not report.stuck and not report.deadlocks
    entry = [e.payload for e in sim.events if e.payload.get("kind") == "entry"]
    assert entry and all(p["primary"] and p["cause_code"] == CAUSE_ENTRY for p in entry)
    late = [p["delay_s"] for p in entry if p["delay_s"] > 0]
    assert late and max(late) > 15 * 60  # the tail reaches beyond the on-time window
