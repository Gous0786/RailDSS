"""Loco failure and signal / block-instrument failure in the injector (issue #146; SCN-04, SCN-07)."""

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


def _secs(ev: EventEnvelope) -> float:
    return (ev.event_time - day_start(DAY)).total_seconds()


def _held(sim: Any, label: str, rid: str) -> list[tuple[float, float]]:
    """``[(from, to)]`` that ``rid`` held ``label``."""
    out, start = [], None
    for t, lab, r, occupied in sim.occupancy_log:
        if lab == label and r == rid:
            if occupied:
                start = t
            elif start is not None:
                out.append((start, t))
                start = None
    return out


def test_a_loco_failure_blocks_the_line_it_happens_on_and_works_the_other_line(world: run.World) -> None:
    _, base = run.run_day(world, DAY, until_s=T0 + 2 * 3600)
    freight = {t.plan.run_id for t in base.trains if t.plan.kind == "freight"}
    in_block = [  # a freight train in an absolute block section at T0
        (label, rid)
        for label in sorted({lab for _, lab, _, _ in base.occupancy_log if lab.startswith("BL:")})
        for rid in sorted(freight)
        if any(a <= T0 < b for a, b in _held(base, label, rid))
    ]
    label, rid = in_block[0]
    d = Disruption(
        incident_id="LOCO-T1",
        kind="loco_failure",
        start_s=T0,
        duration_s=45 * MIN,
        etr_s=T0 + 30 * MIN,
        run_id=f"{rid}@{DAY.isoformat()}",  # a freight load is found by its id without the date
        single_line_working=True,
    )
    seen: list[tuple[str, EventEnvelope]] = []
    keys: list[Any] = []

    def setup(sim: Any) -> None:
        inj = Injector(sim, [d])

        def watch() -> Any:
            yield sim.env.timeout(T0 + 10 * MIN)
            keys.append(dict(sim.reversible))  # single-line working is on while the train stands

        sim.env.process(watch())
        keys.append(inj)

    _, sim = run.run_day(
        world, DAY, until_s=T0 + 2 * 3600, sink=lambda topic, ev: seen.append((topic, ev)), setup=setup
    )
    ops = [ev for topic, ev in seen if topic == "ops.disruptions"]
    assert ops and ops[0].event_type == EventType.DISRUPTION_RAISED
    raised = ops[0]
    block_key = label.removeprefix("BL:")
    assert raised.payload["type"] == "loco_failure" and raised.payload["lines_affected"] == [block_key]
    assert (
        _secs(raised) == T0
        and raised.payload["etr"]
        and raised.payload["cause_code"].startswith(rid.replace(" ", "_"))
    )
    assert raised.location is not None and raised.location.km is not None
    assert ops[-1].event_type == EventType.DISRUPTION_CLEARED and _secs(ops[-1]) >= d.end_s
    stood = [(a, b) for a, b in _held(sim, label, rid) if a <= T0 < b]
    assert stood and stood[0][1] >= d.end_s  # it held its line until relief
    assert keys[1], "the other line was worked both ways during the failure"
    assert not sim.reversible  # and back to normal working after relief


def test_a_block_instrument_failure_adds_paper_line_clear_to_caution_working(world: run.World) -> None:
    block = next(b for b in world.layout.blocks.values() if b.id == "SGDM-PDU")
    kinds = {}
    for name, instrument in (("caution", False), ("plc", True)):
        d = Disruption(
            incident_id=f"SIG-{name}",
            kind="signal_failure",
            start_s=T0,
            duration_s=90 * MIN,
            block_id=block.id,
            block_instrument=instrument,
        )
        _, sim = run.run_day(world, DAY, until_s=T0 + 3 * 3600, setup=lambda s, d=d: Injector(s, [d]))
        kinds[name] = sim
    plc_s = float(world.register.value("twin.injector")["paper_line_clear_min"]) * MIN
    prefix = f"BL:{world.layout.lines[block.id][0].segment}/"
    labels = sorted({lab for _, lab, _, _ in kinds["caution"].occupancy_log if lab.startswith(prefix)})
    entries = [
        (a, lab, rid)
        for lab in labels
        for rid in sorted({r for _, la, r, _ in kinds["caution"].occupancy_log if la == lab})
        for a, _ in _held(kinds["caution"], lab, rid)
        if a >= T0
    ]
    _, lab, rid = min(entries)  # the first train to enter the failed block
    slow = _held(kinds["caution"], lab, rid)
    plc = _held(kinds["plc"], lab, rid)
    caution_s = next(b - a for a, b in slow if a >= T0)
    plc_held = [b - a for a, b in plc if a >= T0]
    assert plc_held and plc_held[0] >= caution_s + plc_s - 1  # the same train waits for its authority


def test_the_options_are_checked(world: run.World) -> None:
    _, sim = run.run_day(world, DAY, until_s=60.0)
    with pytest.raises(ValueError, match="block_instrument"):
        Injector(
            sim,
            [
                Disruption(
                    incident_id="X",
                    kind="tsr",
                    start_s=0,
                    duration_s=60,
                    block_id="SGDM-PDU",
                    speed_kmh=30,
                    block_instrument=True,
                )
            ],
        )
    with pytest.raises(ValueError, match="unknown train"):
        Injector(
            sim, [Disruption(incident_id="Y", kind="loco_failure", start_s=0, duration_s=60, run_id="NOPE")]
        )
