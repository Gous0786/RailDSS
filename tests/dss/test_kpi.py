"""KPI engine: MVP set, policy consistency, deltas and the DSS-vs-baseline comparison (FR-KPI-01..05; #81)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import uuid
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope, EventType
from raildss_dss.kpi import TrainInfo, compute, delta, snapshot_rows
from raildss_params import load
from raildss_twin import displib, kpi_eval, run, scenario
from raildss_twin.sim import Directives

ORIGIN = dt.datetime(2025, 8, 20, tzinfo=dt.UTC)
DAY = dt.date(2025, 8, 20)
_seq = iter(range(1, 10_000))


def ev(
    t: float, rid: str, etype: EventType, payload: dict[str, Any], km: float | None = None
) -> EventEnvelope:
    n = next(_seq)
    return EventEnvelope(
        event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"kpi/{n}"),
        event_type=etype,
        schema_version="1.0",
        source="SIM.TWIN",
        section_id="KTV-PSA",
        network_version="TOY",
        scenario_id="kpi-test",
        seq=n,
        event_time=ORIGIN + dt.timedelta(seconds=t),
        train_run_id=rid,
        location={"km": km, "ne_id": None} if km is not None else None,  # type: ignore[arg-type]
        payload=payload,
    )


def golden_events() -> list[EventEnvelope]:
    occ, delay, state = EventType.TDS_OCCUPANCY, EventType.DELAY_RECORD, EventType.TRAIN_STATE
    return [
        # P: passenger, enters 2 min late, leaves 20 min late
        ev(0, "P", occ, {"tds_id": "SL:A/1", "occupied": True}),
        ev(0, "P", delay, {"station": "A", "kind": "arrival", "delay_s": 120, "primary": False}),
        ev(0, "P", state, {"status": "running", "speed_kmh": 60, "station": "A"}, 0.0),
        ev(3000, "P", state, {"status": "running", "speed_kmh": 60, "station": "B"}, 50.0),
        ev(3000, "P", delay, {"station": "B", "kind": "arrival", "delay_s": 1200, "primary": False}),
        ev(3060, "P", occ, {"tds_id": "SL:B/1", "occupied": False}),
        # F: freight, free run 3600 s, takes 4200 s, stands 5 min waiting once
        ev(100, "F", occ, {"tds_id": "SL:B/2", "occupied": True}),
        ev(100, "F", state, {"status": "running", "speed_kmh": 50, "station": "B"}, 50.0),
        ev(1000, "F", state, {"status": "waiting", "speed_kmh": 0, "station": "C"}, 30.0),
        ev(1300, "F", state, {"status": "running", "speed_kmh": 50, "station": "C"}, 30.0),
        ev(4300, "F", state, {"status": "running", "speed_kmh": 50, "station": "A"}, 0.0),
        ev(4300, "F", occ, {"tds_id": "SL:A/2", "occupied": False}),
    ]


TRAINS = {
    "P": TrainInfo(
        run_id="P",
        kind="passenger",
        category="passenger",
        direction="DN",
        booked_entry_s=-120,
        booked_exit_s=1800,
    ),
    "F": TrainInfo(run_id="F", kind="freight", category="freight_other", direction="UP", free_run_s=3600),
}


def test_the_mvp_kpis_reproduce_hand_computed_values() -> None:
    reg = load()
    r = compute(
        golden_events(), TRAINS, ORIGIN, scope="golden", arm="BL-0", network_version="TOY", register=reg
    )
    w = reg.value("policy.weights")
    k = r.kpis
    assert k["A2_section_punctuality_pct"] == 0.0 and k["A2_lop_count"] == 1  # P on time in, 20 min late out
    assert k["A5_weighted_delay_train_min"] == pytest.approx(
        w["passenger"] * (20 - 2) + w["freight_other"] * 10
    )
    assert k["A6_mean_exit_delay_min"] == 20 and k["A6_p90_exit_delay_min"] == 20
    assert k["A4_mean_arrival_delay_min"] == pytest.approx((2 + 20) / 2)
    assert k["A9_mean_time_lost_min"] == pytest.approx((3060 - 0 - (1800 + 120)) / 60)
    assert k["B1_trains_exited"] == 2 and r.by_direction == {"DN/passenger": 1, "UP/freight": 1}
    assert k["C1_freight_speed_kmh"] == pytest.approx(50 / (4200 / 3600), rel=1e-3)
    assert k["C2_freight_detention_h"] == pytest.approx(round(5 / 60, 2))
    assert k["F1_unplanned_stops"] == 1


def test_changing_policy_weights_changes_a5_and_the_optimiser_objective_consistently() -> None:
    """FR-KPI-03: both read the same policy table."""
    world = run.load_world()
    reg = world.register
    w = dict(reg.value("policy.weights"))
    w2 = {**w, "passenger": w["passenger"] * 2}
    changed = reg.parameters["policy.weights"].model_copy(update={"value": w2})
    reg2 = reg.model_copy(update={"parameters": {**reg.parameters, "policy.weights": changed}})
    base = compute(golden_events(), TRAINS, ORIGIN, scope="g", arm="a", network_version="TOY", register=reg)
    twice = compute(golden_events(), TRAINS, ORIGIN, scope="g", arm="a", network_version="TOY", register=reg2)
    p0 = next(t for t in base.trains if t.run_id == "P").weighted_delay_min
    p1 = next(t for t in twice.trains if t.run_id == "P").weighted_delay_min
    assert p1 == pytest.approx(2 * p0)
    inst0, plans = displib.build_instance(world, DAY, 6 * 3600, 6 * 3600 + 1800)
    inst1, _ = displib.build_instance(
        dataclasses.replace(world, register=reg2), DAY, 6 * 3600, 6 * 3600 + 1800
    )
    kinds = [p.category for p in plans]
    pax = [i for i, c in enumerate(inst0.objective) if kinds[c.train] == "passenger"]
    other = [i for i, c in enumerate(inst0.objective) if kinds[c.train] != "passenger"]
    assert pax, "the window has passenger objective components"
    assert all(inst1.objective[i].coeff == 2 * inst0.objective[i].coeff for i in pax)
    assert all(inst1.objective[i].coeff == inst0.objective[i].coeff for i in other)


def test_delta_names_the_most_affected_trains() -> None:
    reg = load()
    base = compute(
        golden_events(), TRAINS, ORIGIN, scope="g", arm="do-nothing", network_version="TOY", register=reg
    )
    later = [e for e in golden_events()]
    plan = compute(
        [
            *later[:4],
            ev(
                3000,
                "P",
                EventType.DELAY_RECORD,
                {"station": "B", "kind": "arrival", "delay_s": 600, "primary": False},
            ),
            *later[5:],
        ],
        TRAINS,
        ORIGIN,
        scope="g",
        arm="plan",
        network_version="TOY",
        register=reg,
    )
    d = delta(plan, base)
    assert d["against"] == "do-nothing"
    assert d["most_affected"] == [{"run_id": "P", "delta_exit_delay_min": -10.0}]
    assert d["kpis"]["A5_weighted_delay_train_min"] < 0
    rows = snapshot_rows(plan, ORIGIN)
    assert {r["kpi"] for r in rows} == set(plan.kpis) and all(
        r["policy_version"] == reg.version for r in rows
    )


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def _demo_candidate(world: run.World, day: dt.date, day_instance: str | None = None) -> Directives:
    plans, _, _ = run.plans_for(world, day, day_instance)
    freight = next(p for p in plans if p.kind == "freight" and p.entry_s > 12 * 3600)
    return Directives(holds={(freight.run_id, freight.nodes[0].op): freight.entry_s + 600})


def test_the_dss_vs_baseline_comparison_is_produced_per_day(world: run.World) -> None:
    comp = kpi_eval.day_comparison(world, DAY, _demo_candidate(world, DAY))
    assert comp["scope"] == "day 2025-08-20" and set(comp["arms"]) == {"DSS", "BL-0"}
    d = comp["deltas"]["BL-0"]
    assert set(d["kpis"]) == set(comp["arms"]["DSS"]) and len(d["most_affected"]) <= 3


def test_the_dss_vs_baseline_comparison_is_produced_per_scenario(world: run.World) -> None:
    sc = next(s for s in scenario.load_library() if s.executable and s.freight_day)
    comp = kpi_eval.day_comparison(
        world,
        sc.wtt_date,
        _demo_candidate(world, sc.wtt_date, sc.freight_day),
        seed=sc.seed,
        day_instance=sc.freight_day,
        scope=sc.id,
    )
    assert comp["scope"] == sc.id and comp["arms"]["BL-0"]["B1_trains_exited"] > 0
