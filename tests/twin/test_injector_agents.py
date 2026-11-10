"""Disruption injector and station-master agents (FR-SCN-02/03, FR-TWIN-15; issue #53)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope, EventType
from raildss_twin import run
from raildss_twin.agents import Order, StationMasters
from raildss_twin.injector import Disruption, Injector
from raildss_twin.plans import day_start
from raildss_twin.sim import Sim

DAY = dt.date(2025, 8, 20)
T0 = 10 * 3600
MIN = 60


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def _secs(ev: EventEnvelope) -> float:
    return (ev.event_time - day_start(DAY)).total_seconds()


@pytest.fixture(scope="module")
def blocked(world: run.World) -> tuple[Disruption, Any, list[tuple[str, EventEnvelope]], Any]:
    """A 30 min block of a busy absolute-block line from 10:00, announced as 20 min."""
    _, base = run.run_day(world, DAY, until_s=T0 + 3600)
    entries: dict[str, list[float]] = {}
    for t, label, _, occupied in base.occupancy_log:
        if occupied and label.startswith("BL:") and T0 + 60 < t < T0 + 30 * MIN:
            entries.setdefault(label, []).append(t)
    label = max(sorted(entries), key=lambda k: len(entries[k]))
    segment, line_id = label.removeprefix("BL:").split("/")
    block = next(b for b in world.layout.blocks.values() if "~".join(sorted((b.from_op, b.to_op))) == segment)
    d = Disruption(
        incident_id="INC-T1",
        kind="line_block",
        start_s=T0,
        duration_s=30 * MIN,
        etr_s=T0 + 20 * MIN,
        block_id=block.id,
        line_id=line_id,
    )
    seen: list[tuple[str, EventEnvelope]] = []
    _, sim = run.run_day(
        world,
        DAY,
        until_s=T0 + 3600,
        sink=lambda topic, ev: seen.append((topic, ev)),
        setup=lambda s: Injector(s, [d]),
    )
    return d, sim, seen, label


def test_a_scripted_line_block_is_published_on_ops_disruptions_with_the_announced_etr(blocked: Any) -> None:
    d, _, seen, _ = blocked
    ops = [ev for topic, ev in seen if topic == "ops.disruptions"]
    assert [ev.event_type for ev in ops] == [
        EventType.DISRUPTION_RAISED,
        EventType.DISRUPTION_UPDATED,
        EventType.DISRUPTION_CLEARED,
    ]
    raised, updated, cleared = ops
    assert _secs(raised) == d.start_s and raised.payload["incident_id"] == "INC-T1"
    assert raised.payload["etr"].startswith(
        (day_start(DAY) + dt.timedelta(seconds=T0 + 20 * MIN)).isoformat()[:16]
    )
    assert _secs(updated) == d.etr_s  # the announced ETR passed: revised to the real end
    assert _secs(cleared) >= d.end_s
    assert raised.source == "SIM.CONTROL_LOG" and raised.payload["lines_affected"]
    assert all(topic.startswith(("gt.", "ops.", "ctrl.")) for topic, _ in seen)


def test_the_line_block_changes_the_ground_truth_at_the_stated_time_and_place(blocked: Any) -> None:
    d, sim, seen, label = blocked
    taken = [_secs(ev) for topic, ev in seen if topic == "gt.occupancy" and ev.train_run_id is None]
    assert taken and d.start_s <= taken[0] < d.end_s  # taken once the train on it had cleared
    trains_inside = [
        (t, rid)
        for t, lab, rid, occupied in sim.occupancy_log
        if occupied and lab == label and taken[0] <= t < d.end_s
    ]
    assert trains_inside == []


def _setup_orders(world: run.World, holder: dict[str, Any]) -> Any:
    plans, _, _ = run.plans_for(world, DAY)
    train = next(
        p for p in plans if any(n.op == "CPP" and T0 + 15 * MIN < n.arr < T0 + 50 * MIN for n in p.nodes)
    )

    def setup(sim: Sim) -> None:
        sm = StationMasters(sim, seed=7)
        verdicts = {
            sl.line_no: sm.decide(
                Order(
                    order_id="probe",
                    station="CPP",
                    run_id=train.run_id,
                    action="receive_on_line",
                    line_no=sl.line_no,
                )
            )
            for sl in world.layout.station_lines["CPP"]
        }
        ok = [no for no, why in verdicts.items() if why is None]
        assert len(ok) >= 2, verdicts
        loop_a, loop_b = ok[0], ok[1]
        forbidden = next((no for no, why in verdicts.items() if why == "RECEPTION_NOT_PERMITTED"), None)
        Injector(
            sim,
            [
                Disruption(
                    incident_id="INC-PF",
                    kind="point_failure",
                    start_s=T0 - 10 * MIN,
                    duration_s=3600,
                    station="CPP",
                    line_no=loop_a,
                )
            ],
        )
        orders = [
            Order(
                order_id="O-1", station="CPP", run_id=train.run_id, action="receive_on_line", line_no=loop_a
            ),
            Order(
                order_id="O-2", station="CPP", run_id=train.run_id, action="receive_on_line", line_no=loop_b
            ),
        ]
        if forbidden is not None:
            orders.append(
                Order(
                    order_id="O-3",
                    station="CPP",
                    run_id=train.run_id,
                    action="receive_on_line",
                    line_no=forbidden,
                )
            )

        def issue() -> Any:
            yield sim.env.timeout(T0)
            for o in orders:
                sm.submit(o)

        sim.env.process(issue())
        holder.update(sm=sm, train=train, loop_b=loop_b, forbidden=forbidden)

    return setup


def test_an_order_for_an_occupied_loop_is_declined_and_one_for_a_free_loop_is_acknowledged(
    world: run.World,
) -> None:
    holder: dict[str, Any] = {}
    seen: list[tuple[str, EventEnvelope]] = []
    run.run_day(
        world,
        DAY,
        until_s=T0 + 3600,
        sink=lambda topic, ev: seen.append((topic, ev)),
        setup=_setup_orders(world, holder),
    )
    sm: StationMasters = holder["sm"]
    by_id = {a.order_id: a for a in sm.answers}
    assert by_id["O-1"].status == "DECLINED" and by_id["O-1"].reason_code == "LOOP_OCCUPIED"
    assert by_id["O-2"].status == "ACKNOWLEDGED" and by_id["O-2"].reason_code is None
    for a in sm.answers:
        assert sm.lo <= a.latency_s <= sm.hi  # 30-90 s (hitl.sm_ack_latency_s)
    if holder["forbidden"] is not None:
        assert by_id["O-3"].reason_code == "RECEPTION_NOT_PERMITTED"
    assert sm.sim.directives.lines[(holder["train"].run_id, "CPP")] == holder["loop_b"]
    acks = [ev for topic, ev in seen if topic == "ctrl.order_acks"]
    assert {ev.payload["order_id"] for ev in acks} == set(by_id)
    assert all(ev.event_type is EventType.ORDER_ACK and ev.source == "SIM.SM" for ev in acks)


def test_an_injected_disruption_must_name_a_real_place(world: run.World) -> None:
    def setup(sim: Sim) -> None:
        with pytest.raises(ValueError, match="unknown block"):
            Injector(
                sim,
                [Disruption(incident_id="X", kind="line_block", start_s=0, duration_s=60, block_id="NOPE")],
            )

    run.run_day(world, DAY, until_s=60, setup=setup)
