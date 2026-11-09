"""Twin skeleton tests (issue #20 acceptance criteria): one real day under BL-0.

The full-day tests share one run of 20 Aug 2025 (WTT for the date plus the real freight day).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import time
from types import SimpleNamespace
from typing import Any

import pytest
import simpy

from raildss_common.envelope import EventEnvelope
from raildss_twin import run
from raildss_twin.clock import Clock
from raildss_twin.layout import pair_key
from raildss_twin.plans import day_start
from raildss_twin.sim import Option, Sim, occupancy_violations

DAY = dt.date(2025, 8, 20)
HEADLESS_LIMIT_S = 240  # FR-TWIN-12: 24 h on the full section in <= 4 min


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def day_run(world: run.World) -> tuple[run.RunReport, run.Sim]:
    return run.run_day(world, DAY, keep_events=True)


def test_real_day_completes_headless_within_the_limit(day_run: Any) -> None:
    report, _ = day_run
    assert report.completed, (report.stuck[:3], report.deadlocks[:1])
    assert report.wall_s <= HEADLESS_LIMIT_S
    assert report.network_version == "KTV-PSA-2025-08"


def test_every_train_enters_and_exits_or_is_listed_with_a_reason(day_run: Any) -> None:
    report, sim = day_run
    assert report.entered == report.planned > 100
    assert report.exited + len(report.stuck) == report.planned
    assert all(s.reason for s in report.stuck)
    assert all(u["reason"] for u in report.unsupported)
    for t in sim.trains:
        ends = (t.plan.nodes[0].op, t.plan.nodes[-1].op)
        assert t.status == "done" and t.entered and t.op == ends[1]


def test_no_unit_resource_is_held_by_two_trains(day_run: Any) -> None:
    _, sim = day_run
    assert len(sim.occupancy_log) > 10_000
    capacity = {
        f"SL:{op}/{sl.line_no}": sl.capacity_trains for op, v in sim.layout.station_lines.items() for sl in v
    }
    assert occupancy_violations(sim.occupancy_log, lambda label: capacity.get(label, 1)) == []


def test_the_invariant_check_detects_a_double_occupation() -> None:
    log = [(0.0, "BL:A~B/DN", "t1", True), (5.0, "BL:A~B/DN", "t2", True), (9.0, "BL:A~B/DN", "t1", False)]
    assert occupancy_violations(log, lambda _: 1) == ["BL:A~B/DN holds ['t1', 't2'] at 5 s"]


def test_events_are_valid_ground_truth_envelopes(day_run: Any) -> None:
    report, sim = day_run
    assert set(report.events) == {"gt.state", "gt.occupancy", "gt.signal", "gt.delay"}
    assert sum(report.events.values()) == len(sim.events)
    seqs = [e.seq for e in sim.events]
    assert seqs == list(range(1, len(seqs) + 1))
    assert all(isinstance(e, EventEnvelope) and e.source == "SIM.TWIN" for e in sim.events)
    assert all(e.scenario_id == "aug25-day-20-bl0" for e in sim.events)


def test_same_seed_gives_the_same_trace_hash(world: run.World, day_run: Any) -> None:
    report, _ = day_run
    again, _ = run.run_day(world, DAY)
    assert again.trace_hash == report.trace_hash


def test_a_different_seed_gives_a_different_hash(world: run.World) -> None:
    a, _ = run.run_day(world, DAY, seed=1, until_s=3600)
    b, _ = run.run_day(world, DAY, seed=2, until_s=3600)
    c, _ = run.run_day(world, DAY, seed=1, until_s=3600)
    assert a.trace_hash == c.trace_hash != b.trace_hash


def test_a_window_reports_trains_in_progress_not_stuck(world: run.World) -> None:
    report, _ = run.run_day(world, DAY, until_s=3600)
    assert report.in_progress > 0 and not report.stuck and report.sim_end_s == 3600


def test_passenger_trains_keep_to_booked_time(day_run: Any) -> None:
    """Booked trains never leave a stop before the booked departure (BL-0, timetable order)."""
    _, sim = day_run
    departures = [e.payload["delay_s"] for e in sim.events if e.payload.get("kind") == "departure"]
    assert departures and min(departures) >= -1.0


# ---------------------------------------------------------------- engine primitives


def _toy_sim(world: run.World) -> Sim:
    return Sim(
        world.layout, world.timing, world.register, [], DAY, origin=day_start(DAY), scenario_id="toy", seed=0
    )


def _train(name: str) -> Any:
    from raildss_twin.sim import Train

    plan = SimpleNamespace(run_id=name, kind="freight")
    t = Train(plan)  # type: ignore[arg-type]
    t.entered, t.status = True, "running"
    return t


def test_deadlock_is_detected_and_reported_with_its_location(world: run.World) -> None:
    sim = _toy_sim(world)
    t1, t2 = _train("T1"), _train("T2")
    t1.block, t2.block = "A-B", "B-A"

    def proc(t: Any, first: str, second: str) -> Any:
        yield from sim.acquire(t, [Option(first)])
        yield sim.env.timeout(1)
        yield from sim.acquire(t, [Option(second)])

    sim.trains = [t1, t2]
    sim.env.process(proc(t1, "R1", "R2"))
    sim.env.process(proc(t2, "R2", "R1"))
    sim.env.run(until=100)
    sim.check_deadlock()
    assert len(sim.deadlocks) == 1
    stuck = {s.run_id: s for s in sim.deadlocks[0].trains}
    assert set(stuck) == {"T1", "T2"}
    assert stuck["T1"].waiting_for == "R2" and stuck["T1"].blocked_by == ["T2"]
    assert stuck["T2"].location == "B-A (towards )" and stuck["T2"].held == ["R2"]


def test_a_waiting_train_that_will_be_served_is_not_a_deadlock(world: run.World) -> None:
    sim = _toy_sim(world)
    t1, t2 = _train("T1"), _train("T2")
    sim.trains = [t1, t2]

    def holder() -> Any:
        yield from sim.acquire(t1, [Option("R1")])
        yield sim.env.timeout(50)
        sim.release(t1, "R1")

    def waiter() -> Any:
        yield sim.env.timeout(1)
        yield from sim.acquire(t2, [Option("R1")])

    sim.env.process(holder())
    sim.env.process(waiter())
    sim.env.run(until=10)
    sim.check_deadlock()
    assert sim.deadlocks == [] and t2.waiting is not None
    sim.env.run(until=100)
    assert "R1" in t2.held


def test_a_reversible_line_is_not_entered_against_oncoming_traffic(world: run.World) -> None:
    sim = _toy_sim(world)
    dn, up = _train("DN"), _train("UP")
    sim.trains = [dn, up]
    key = pair_key("KUK", "VZM") + "/MID"
    log: list[tuple[str, float]] = []

    def down() -> Any:
        yield from sim.acquire(dn, [Option("SB:a#1", 1, key, "DN")])
        yield sim.env.timeout(30)
        sim.release(dn, "SB:a#1")

    def upward() -> Any:
        yield sim.env.timeout(1)
        yield from sim.acquire(up, [Option("SB:a#9", 1, key, "UP")])  # a different, free sub-block
        log.append(("UP granted", sim.env.now))

    sim.env.process(down())
    sim.env.process(upward())
    sim.env.run(until=200)
    assert log == [("UP granted", 30.0)]


# ---------------------------------------------------------------- clock


def test_clock_validates_speed_and_runs_in_real_time() -> None:
    with pytest.raises(ValueError, match="between 1 and 60"):
        Clock(61.0, 60.0)
    with pytest.raises(ValueError, match="between 1 and 60"):
        Clock(0.5, 60.0)
    assert isinstance(Clock(None, 60.0).environment(), simpy.Environment)
    clock = Clock(60.0, 60.0)
    env = clock.environment()
    start = time.monotonic()
    env.run(until=60)  # one minute of simulated time at x60 is about one second
    assert 0.7 < time.monotonic() - start < 3.0
    clock.set_speed(30.0)
    assert clock.speed == 30.0
    with pytest.raises(ValueError, match="between 1 and 60"):
        clock.set_speed(90.0)


def test_clock_pause_holds_the_simulation() -> None:
    clock = Clock(60.0, 60.0)
    env = clock.environment()
    clock.pause()
    import threading

    threading.Timer(0.4, clock.resume).start()
    start = time.monotonic()
    env.run(until=5)
    assert time.monotonic() - start >= 0.35


# ---------------------------------------------------------------- reversible line: same-instant races


def _head_ons(sim: Sim, directions: dict[str, str], labels: set[str]) -> list[str]:
    """Moments at which trains of both directions hold sub-blocks of the one reversible line."""
    held: dict[str, str] = {}
    problems: list[str] = []
    for time_s, label, train, occupied in sim.occupancy_log:
        if label not in labels:
            continue
        if occupied:
            held[train] = directions[train]
            if len(set(held.values())) > 1:
                problems.append(f"{sorted(held)} at {time_s:.0f} s")
        else:
            held.pop(train, None)
    return problems


def _mid_scenario(
    world: run.World, script: list[tuple[str, str, float, float]]
) -> tuple[Sim, dict[str, float]]:
    """Trains ``(name, direction, request time, hold time)``; DN enters at sub-block #1, UP at #9."""
    sim = _toy_sim(world)
    key = pair_key("KUK", "VZM") + "/MID"
    granted: dict[str, float] = {}
    directions = {name: d for name, d, _, _ in script}
    sim.trains = [_train(name) for name, *_ in script]

    def proc(t: Any, d: str, at: float, hold: float) -> Any:
        yield sim.env.timeout(at)
        label = "SB:a#1" if d == "DN" else "SB:a#9"
        yield from sim.acquire(t, [Option(label, 1, key, d)])
        granted[t.plan.run_id] = sim.env.now
        yield sim.env.timeout(hold)
        sim.release(t, label)

    for t, (_, d, at, hold) in zip(sim.trains, script, strict=True):
        sim.env.process(proc(t, d, at, hold))
    sim.env.run(until=500)
    sim._directions = directions  # type: ignore[attr-defined]
    return sim, granted


@pytest.mark.parametrize("order", [("DN", "UP"), ("UP", "DN")])
def test_opposing_trains_at_the_same_instant_do_not_both_enter_the_reversible_line(
    world: run.World, order: tuple[str, str]
) -> None:
    """Review of PR #46: the gate was written only after a train resumed, so two trains asking in the same
    instant, for different sub-blocks of the one line, both got in."""
    first, second = order
    sim, granted = _mid_scenario(world, [("T1", first, 0.0, 30.0), ("T2", second, 0.0, 30.0)])
    labels = {"SB:a#1", "SB:a#9"}
    assert _head_ons(sim, {"T1": first, "T2": second}, labels) == []
    assert granted == {"T1": 0.0, "T2": 30.0}


def test_a_queued_train_keeps_the_line_from_oncoming_traffic_when_the_last_user_leaves(
    world: run.World,
) -> None:
    """X (DN) holds #1 until 10 s. A (DN) queues for #1. At 10 s, as X leaves and A is granted, B (UP) asks
    for the far sub-block in the same instant: it must wait for A, not slip in behind X."""
    sim, granted = _mid_scenario(
        world, [("X", "DN", 0.0, 10.0), ("A", "DN", 1.0, 20.0), ("B", "UP", 10.0, 5.0)]
    )
    assert _head_ons(sim, {"X": "DN", "A": "DN", "B": "UP"}, {"SB:a#1", "SB:a#9"}) == []
    assert granted == {"X": 0.0, "A": 10.0, "B": 30.0}
    assert sim.gates == {} and all(not t.claims for t in sim.trains)


def test_same_direction_trains_still_follow_each_other_on_the_reversible_line(world: run.World) -> None:
    sim, granted = _mid_scenario(world, [("T1", "DN", 0.0, 30.0), ("T2", "DN", 0.0, 5.0)])
    assert granted == {"T1": 0.0, "T2": 30.0}  # one sub-block holds one train; the gate does not add a wait
    assert sim.gates == {}


def test_a_train_waiting_only_for_oncoming_traffic_is_not_reported_as_a_deadlock(world: run.World) -> None:
    sim = _toy_sim(world)
    key = pair_key("KUK", "VZM") + "/MID"
    t1, t2 = _train("DN"), _train("UP")
    sim.trains = [t1, t2]

    def down() -> Any:
        yield from sim.acquire(t1, [Option("SB:a#1", 1, key, "DN")])
        yield sim.env.timeout(100)
        sim.release(t1, "SB:a#1")

    def up() -> Any:
        yield from sim.acquire(t2, [Option("SB:a#9", 1, key, "UP")])

    sim.env.process(down())
    sim.env.process(up())
    sim.env.run(until=50)
    sim.check_deadlock()
    assert sim.deadlocks == [] and t2.waiting_gates == [key]
    assert sim.stuck_info(t2, "x").blocked_by == ["DN"]
    sim.env.run(until=200)
    assert "SB:a#9" in t2.held


# ---------------------------------------------------------------- trains running at the start (DR-0002 F10)


def test_no_train_is_unsupported_on_any_of_the_27_days(world: run.World) -> None:
    for d in range(1, 28):
        _, unsupported, _ = run.plans_for(world, dt.date(2025, 8, d))
        assert unsupported == [], (d, unsupported)


def test_a_train_cut_by_midnight_inside_a_block_is_resumed_on_that_block(
    world: run.World, day_run: Any
) -> None:
    """22501 reaches its first halt of 20 Aug after midnight: it starts the day inside the block before it."""
    report, sim = day_run
    train = next(t for t in sim.trains if t.plan.run_id == "22501@2025-08-19")
    assert train.plan.entry_s < 0 and train.status == "done"
    first_block = train.plan.nodes[0].op
    positions, _ = run.state_at(world, DAY, 1.0)
    pos = next(p for p in positions if p.run_id == "22501@2025-08-19")
    assert pos.block is not None and pos.station == first_block and pos.towards != first_block
    assert any(label.startswith("BL:") for label in pos.held)
    assert report.completed and report.unsupported == []


def test_a_train_resumed_in_an_automatic_block_holds_the_sub_block_it_is_on(world: run.World) -> None:
    plans, _, _ = run.plans_for(world, DAY)
    plan = next(
        p for p in plans if p.kind == "passenger" and [n.op for n in p.nodes[:3]] == ["KTV", "KPL", "ALM"]
    )
    mid = (plan.nodes[0].dep + plan.nodes[1].arr) / 2  # halfway between KTV and KPL on the booked plan
    shifted = dataclasses.replace(
        plan,
        run_id="RESUME-1",
        entry_s=plan.entry_s - mid,
        nodes=[dataclasses.replace(n, arr=n.arr - mid, dep=n.dep - mid) for n in plan.nodes],
    )
    report, sim = run.run_day(world, DAY, plans=[shifted], keep_events=True)
    assert report.completed and report.exited == 1
    first = next(label for _, label, train, occupied in sim.occupancy_log if occupied and train == "RESUME-1")
    sub_block = int(first.rsplit("#", 1)[1])
    assert first.startswith("SB:") and 1 < sub_block < 10  # mid-block: neither the first nor the last of 11
    reached = [
        e for e in sim.events if e.payload.get("station") == "KPL" and e.payload.get("kind") == "arrival"
    ]
    assert reached and abs(reached[0].payload["delay_s"]) <= 60  # on the booked plan


def test_a_window_at_noon_places_every_running_train_on_its_block(world: run.World, day_run: Any) -> None:
    noon = 12 * 3600.0
    _, full = day_run
    running = {t.plan.run_id for t in full.trains if t.plan.entry_s <= noon < (t.exit_s or 0.0)}
    positions, report = run.state_at(world, DAY, noon)
    assert {p.run_id for p in positions} == running and len(running) > 5
    assert report.in_progress == len(running) and report.sim_end_s == noon
    for p in positions:
        assert p.station in world.layout.ops
        if p.block is not None:
            block = world.layout.blocks[p.block]
            assert p.towards == block.to_op
            assert any(label.startswith(("BL:", "SB:")) for label in p.held)
            lo, hi = sorted((world.layout.km(block.from_op), world.layout.km(block.to_op)))
            assert lo - 1e-6 <= p.km <= hi + 1e-6
        else:
            assert any(label.startswith("SL:") for label in p.held) or p.status == "waiting"
