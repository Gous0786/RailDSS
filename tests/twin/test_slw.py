"""Temporary single-line working in the twin (spec FR-TWIN-18; issue #54)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_twin import run
from raildss_twin.injector import Disruption, Injector
from raildss_twin.plans import day_start
from raildss_twin.sim import Sim
from raildss_verifier import network
from raildss_verifier.auditor import AuditReport, TrainMeta, audit

DAY = dt.date(2025, 8, 20)
START_S, LENGTH_S = 10 * 3600, 3 * 3600
NETWORK = "KTV-PSA-2025-08"


def window(block: str = "CPP-SGDM", line: str = "DN", **kw: Any) -> Disruption:
    return Disruption(
        incident_id=f"SLW-{block}",
        kind="line_block",
        start_s=START_S,
        duration_s=LENGTH_S,
        block_id=block,
        line_id=line,
        single_line_working=True,
        **kw,
    )


def run_with(world: run.World, d: Disruption) -> tuple[run.RunReport, Sim]:
    return run.run_day(world, DAY, keep_events=True, setup=lambda sim: Injector(sim, [d]))


def audit_under(sim: Sim, net: network.Network) -> AuditReport:
    metas = {
        t.plan.run_id: TrainMeta(
            kind=t.plan.kind,
            length_m=t.plan.template.length_m,
            electric=t.plan.template.loco.traction == "electric",
            booked_dep={n.op: n.dep for n in t.plan.nodes if n.stop} if t.plan.timetabled else {},
        )
        for t in sim.trains
    }
    return audit(sim.events, metas, net, day_start(DAY))


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def net() -> network.Network:
    return network.load(NETWORK)


@pytest.fixture(scope="module")
def cpp_sgdm(world: run.World) -> tuple[run.RunReport, Sim]:
    return run_with(world, window())


def test_a_window_on_cpp_sgdm_runs_a_real_day_without_deadlock_or_violations(
    cpp_sgdm: tuple[run.RunReport, Sim], net: network.Network
) -> None:
    report, sim = cpp_sgdm
    assert report.completed and not report.stuck and not report.deadlocks
    audited = audit_under(sim, network.single_line_working(net, "CPP-SGDM", "DN"))
    assert audited.executed_violations == 0, audited.by_rule
    # the sim is back to normal after the window
    assert sim.reversible == {} and sim.borrowed == {}


def test_trains_of_both_directions_use_the_one_line_and_cross_at_stations(
    cpp_sgdm: tuple[run.RunReport, Sim],
) -> None:
    _, sim = cpp_sgdm
    origin = day_start(DAY)
    used = {"DN": 0, "UP": 0}
    for e in sim.events:
        p = e.payload
        when = (e.event_time - origin).total_seconds()
        if p.get("mode") == "send" and p.get("block_line") == "UP" and START_S <= when <= START_S + LENGTH_S:
            if p["block"] == "CPP-SGDM":
                used["DN"] += 1  # a down train on the up line: the working line
            elif p["block"] == "SGDM-CPP":
                used["UP"] += 1
    assert used["DN"] > 0 and used["UP"] > 0, used
    assert not any(
        p.get("mode") == "send" and p.get("block") == "CPP-SGDM" and p.get("block_line") == "DN"
        for e in sim.events
        if START_S + 600 < (e.event_time - origin).total_seconds() < START_S + LENGTH_S
        for p in [e.payload]
    ), "no train uses the blocked line during the window"
    assert len(sim.crossings) >= 1


def test_crossings_happen_only_at_stations_with_a_crossing_facility_and_a_long_enough_line(
    cpp_sgdm: tuple[run.RunReport, Sim], world: run.World
) -> None:
    _, sim = cpp_sgdm
    stations = {o.code: o for o in world.layout.ops.values()}
    lengths = {t.plan.run_id: t.plan.template.length_m for t in sim.trains}
    assert sim.crossings
    for c in sim.crossings:
        assert stations[c.station].crossing_facility and not stations[c.station].is_halt, c
        line = next(sl for sl in world.layout.station_lines[c.station] if sl.line_no == c.line)
        assert line.csr_m is not None and line.csr_m >= lengths[c.run_id], c


def test_without_the_single_line_working_overlay_the_borrowed_connections_are_flagged(
    cpp_sgdm: tuple[run.RunReport, Sim], net: network.Network
) -> None:
    """The overlay is what makes the window auditable: against the ordinary network a down train on the
    up line has no Connections edge, which is the point of the independent check."""
    _, sim = cpp_sgdm
    assert audit_under(sim, net).by_rule.get("VER-CON-01", 0) > 0


def test_the_overlay_works_the_other_line_both_ways_and_leaves_automatic_lines_alone(
    net: network.Network,
) -> None:
    slw = network.single_line_working(net, "CPP-SGDM", "DN")
    for block in ("CPP-SGDM", "SGDM-CPP"):
        assert slw.lines[(block, "UP")].direction == "BOTH"
        assert slw.lines[(block, "DN")].direction == "DN"
    assert ("CPP", 2, "CPP-SGDM", "UP", "send") in slw.connections
    assert ("SGDM", 2, "CPP-SGDM", "UP", "receive") in slw.connections
    assert slw.lines[("GVI-CPP", "UP")] == net.lines[("GVI-CPP", "UP")]  # other sections untouched
    auto = network.single_line_working(net, "KUK-VZM", "DN")
    assert auto.lines == net.lines  # automatic block: no reversal, the MID line already works both ways


def test_a_window_over_a_range_of_block_sections(world: run.World, net: network.Network) -> None:
    d = window("CPP-SGDM", through_block="SGDM-PDU")
    report, sim = run_with(world, d)
    assert report.completed and not report.stuck and not report.deadlocks
    audited = audit_under(
        sim,
        network.single_line_working(network.single_line_working(net, "CPP-SGDM", "DN"), "SGDM-PDU", "DN"),
    )
    assert audited.executed_violations == 0, audited.by_rule
    assert sim.reversible == {} and sim.borrowed == {}


def test_a_range_must_run_forward_in_the_blocks_direction(world: run.World) -> None:
    for bad in ("GVI-CPP", "SGDM-CPP", "NOPE"):
        _, sim = run.run_day(world, DAY, until_s=60)
        with pytest.raises(ValueError, match=r"not a block section|lies before"):
            Injector(sim, [window("CPP-SGDM", through_block=bad)])
    _, sim = run.run_day(world, DAY, until_s=60)
    with pytest.raises(ValueError, match="one line"):
        Injector(sim, [window("CPP-SGDM", line=None)])  # type: ignore[arg-type]


def test_a_window_on_one_ktv_vzm_line_works_through_the_mid_line(world: run.World) -> None:
    report, sim = run_with(world, window("KUK-VZM", "DN"))
    assert report.completed and not report.stuck and not report.deadlocks
    assert sim.crossings == [] and sim.reversible == {}  # the automatic lines are not reversed
