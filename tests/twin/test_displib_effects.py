"""Known incidents in the IR and in the planner's twin runs (issue #155; FR-OPT-08/09)."""

from __future__ import annotations

import datetime as dt

import pytest

from raildss_twin import displib, run
from raildss_twin.injector import Disruption
from raildss_twin.sim import Directives

DAY = dt.date(2025, 8, 20)
T0 = 10 * 3600.0
END = T0 + 3600.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def plain(world: run.World) -> displib.Instance:
    return displib.build_instance(world, DAY, T0, END)[0]


def _tsr(block: str, start: float = T0, speed: float = 30.0) -> Disruption:
    return Disruption(
        incident_id="TSR-T", kind="tsr", start_s=start, duration_s=7200, block_id=block, speed_kmh=speed
    )


def test_effects_are_the_incidents_active_in_the_window(world: run.World) -> None:
    out = Disruption(
        incident_id="PF", kind="point_failure", start_s=T0, duration_s=60, station="CPP", line_no=4
    )
    eff = displib.effects_of(
        world, [_tsr("SGDM-PDU"), _tsr("CPP-SGDM", start=END + 1), out], {"X@d": 300.0}, T0, END
    )
    assert {r[0] for r in eff.restrictions} == {world.layout.lines["SGDM-PDU"][0].segment}
    assert all(r[2] == 30.0 for r in eff.restrictions)
    assert eff.out_of_use == frozenset({("CPP", 4)}) and dict(eff.entry_delays) == {"X@d": 300.0}
    assert eff.recovery == int(world.register.value("opt.recovery")["exit_weight"])
    plc = Disruption(
        incident_id="SIG",
        kind="signal_failure",
        start_s=T0,
        duration_s=60,
        block_id="SGDM-PDU",
        block_instrument=True,
    )
    (sig,) = {r[2:] for r in displib.effects_of(world, [plc], None, T0, END).restrictions}
    cfg = world.register.value("twin.injector")
    assert sig == (float(cfg["signal_failure_speed_kmh"]), float(cfg["paper_line_clear_min"]) * 60)


def test_a_speed_restriction_lengthens_the_block_operations_of_its_line(
    world: run.World, plain: displib.Instance
) -> None:
    slow = displib.build_instance(world, DAY, T0, END, incidents=[_tsr("SGDM-PDU")])[0]
    segment = world.layout.lines["SGDM-PDU"][0].segment

    def durations(inst: displib.Instance) -> list[int]:
        return [
            o.min_duration
            for t in inst.trains
            for o in t.operations
            if o.kind == "block" and o.resources[0].resource.startswith(f"BL:{segment}/")
        ]

    assert durations(plain) and len(durations(slow)) == len(durations(plain))
    assert all(s >= p for s, p in zip(durations(slow), durations(plain), strict=True))
    assert sum(durations(slow)) > sum(durations(plain))


def test_a_loop_out_of_use_and_a_late_entry_change_the_instance(
    world: run.World, plain: displib.Instance
) -> None:
    used = sorted(
        {r.resource for t in plain.trains for o in t.operations if o.kind == "station" for r in o.resources}
    )
    station, _, line = used[len(used) // 2].removeprefix("SL:").partition("/")
    fail = Disruption(
        incident_id="PF",
        kind="point_failure",
        start_s=T0,
        duration_s=3600,
        station=station,
        line_no=int(line),
    )
    first = plain.trains[0]
    late = {first.id: 600.0}
    inst = displib.build_instance(world, DAY, T0, END, incidents=[fail], entry_delays=late)[0]
    res = {r.resource for t in inst.trains for o in t.operations for r in o.resources}
    assert f"SL:{station}/{line}" not in res
    entry = next(t for t in inst.trains if t.id == first.id).operations[0]
    assert entry.start_lb >= first.operations[0].start_lb + 600 - 1


def test_the_exit_component_carries_the_recovery_weight(world: run.World, plain: displib.Instance) -> None:
    weight = int(world.register.value("opt.recovery")["exit_weight"])
    by_train: dict[int, list[float]] = {}
    for c in plain.objective:
        by_train.setdefault(c.train, []).append(c.coeff)
    assert weight > 1 and any(max(cs) == weight * min(cs) for cs in by_train.values() if len(cs) > 1)


def test_a_plan_run_applies_the_incidents_it_knows_of(world: run.World) -> None:
    base, _ = run.run_day(world, DAY, until_s=END)
    slow, sim = run.run_day(
        world, DAY, until_s=END, directives=Directives(incidents=[_tsr("SGDM-PDU", speed=15.0)])
    )
    assert sim.restrictions and slow.trace_hash != base.trace_hash
