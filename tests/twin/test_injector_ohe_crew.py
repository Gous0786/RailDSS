"""OHE power block / trip and crew running out of hours in the injector (issue #147; SCN-08, -09, -10)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope, EventType
from raildss_twin import run
from raildss_twin.injector import Disruption, Injector
from raildss_twin.plans import day_start

DAY = dt.date(2025, 8, 20)
T0 = 10 * 3600.0
MIN = 60.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def base(world: run.World) -> Any:
    return run.run_day(world, DAY, until_s=T0 + 3 * 3600)[1]


def _secs(ev: EventEnvelope) -> float:
    return (ev.event_time - day_start(DAY)).total_seconds()


def _held(sim: Any, label: str) -> list[tuple[str, float, float]]:
    """``[(run id, from, to)]`` of every train on ``label``."""
    out, open_ = [], {}
    for t, lab, rid, occupied in sim.occupancy_log:
        if lab != label or rid is None:
            continue
        if occupied:
            open_[rid] = t
        elif rid in open_:
            out.append((rid, open_.pop(rid), t))
    return out


def _running_at(world: run.World, base: Any) -> tuple[str, str, str]:
    """(block id of the DN COA block, BL label, run id) of a train on an absolute block line at T0."""
    for b in sorted(world.layout.blocks.values(), key=lambda x: x.id):
        if b.direction != "DN":
            continue
        for ln in world.layout.lines[b.id]:
            if ln.system == "AUTO" or ln.direction != "DN":
                continue
            label = f"BL:{ln.key}"
            for rid, a, z in _held(base, label):
                if a < T0 < z - 120:
                    return b.id, label, rid
    raise AssertionError("no train on an absolute DN block line at T0")


def _run(world: run.World, d: Disruption) -> tuple[Any, list[EventEnvelope]]:
    seen: list[tuple[str, EventEnvelope]] = []
    _, sim = run.run_day(
        world,
        DAY,
        until_s=T0 + 3 * 3600,
        sink=lambda topic, ev: seen.append((topic, ev)),
        setup=lambda s: Injector(s, [d]),
    )
    return sim, [ev for topic, ev in seen if topic == "ops.disruptions"]


def test_an_ohe_trip_stops_the_train_in_the_zone_and_keeps_others_out(world: run.World, base: Any) -> None:
    block, label, rid = _running_at(world, base)
    d = Disruption(incident_id="TRIP", kind="ohe_trip", start_s=T0, duration_s=30 * MIN, block_id=block)
    sim, ops = _run(world, d)
    assert [ev.event_type for ev in ops] == [EventType.DISRUPTION_RAISED, EventType.DISRUPTION_CLEARED]
    assert (
        ops[0].payload["type"] == "ohe_trip" and label.removeprefix("BL:") in ops[0].payload["lines_affected"]
    )
    assert _secs(ops[0]) == T0
    stopped = [(a, z) for r, a, z in _held(sim, label) if r == rid and a < T0]
    assert stopped and stopped[0][1] >= d.end_s  # it stood in the zone until the power came back
    entered = [r for r, a, _ in _held(sim, label) if T0 <= a < d.end_s]
    assert entered == []  # nobody entered the zone meanwhile


def test_an_overrunning_power_block_is_revised_and_keeps_trains_out(world: run.World, base: Any) -> None:
    block, label, _ = _running_at(world, base)
    d = Disruption(
        incident_id="BLOCK",
        kind="ohe_block",
        start_s=T0,
        duration_s=60 * MIN,
        etr_s=T0 + 30 * MIN,  # announced for 30 min, overruns to 60
        block_id=block,
    )
    sim, ops = _run(world, d)
    assert [ev.event_type for ev in ops] == [
        EventType.DISRUPTION_RAISED,
        EventType.DISRUPTION_UPDATED,
        EventType.DISRUPTION_CLEARED,
    ]
    assert _secs(ops[1]) == d.etr_s and ops[0].payload["type"] == "ohe_block"
    entered = [r for r, a, _ in _held(sim, label) if T0 + 5 * MIN <= a < d.end_s]
    assert entered == []


def test_a_crew_out_of_hours_holds_the_train_at_the_station(world: run.World, base: Any) -> None:
    deps = [
        (t, lab.removeprefix("SL:").split("/")[0], rid)
        for t, lab, rid, occupied in base.occupancy_log
        if not occupied
        and lab.startswith("SL:")
        and rid
        and "@" not in rid
        and T0 + 10 * MIN < t < T0 + 60 * MIN
    ]
    t_dep, station, rid = sorted(deps)[0]  # a freight train leaving a station soon after T0
    d = Disruption(
        incident_id="CREW",
        kind="crew_hours",
        start_s=T0,
        duration_s=90 * MIN,
        run_id=rid,
        station=station,
    )
    sim, ops = _run(world, d)
    assert ops[0].payload["type"] == "crew_hours" and ops[0].location is not None
    assert ops[0].location.ne_id == station and ops[0].payload["cause_code"]
    gone = min(
        t
        for t, lab, r, occupied in sim.occupancy_log
        if r == rid and not occupied and lab.startswith(f"SL:{station}/")
    )
    assert t_dep < d.end_s <= gone  # without the crew it left at t_dep; it waits for relief
