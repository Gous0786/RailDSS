"""Rolling windows of the solver IR and DISPLIB 2025 conformance (FR-OPT-01; issue #74).

A window starting mid-day is built from a snapshot of what every train occupies at its start, taken
from the twin's occupancy log (the same view the DSS state ``S_t`` gives). Trains already running are
frozen at their current operations; the independent evaluator must accept the twin's schedule.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_twin import displib, run
from raildss_twin.displib_check import evaluate, schedule_from_trace

DAY = dt.date(2025, 8, 20)
START_S = 12 * 3600
END_S = START_S + 2 * 3600


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def day(world: run.World) -> Any:
    report, sim = run.run_day(world, DAY)
    assert report.completed
    return sim


@pytest.fixture(scope="module")
def snapshot(world: run.World, day: Any) -> displib.Snapshot:
    return displib.snapshot_from_occupancy(day.occupancy_log, START_S, world.layout.network_version)


@pytest.fixture(scope="module")
def window(world: run.World, snapshot: displib.Snapshot) -> tuple[displib.Instance, list[Any]]:
    return displib.build_instance(world, DAY, START_S, END_S, snapshot=snapshot)


def test_a_mid_day_window_converts_with_the_running_trains_frozen(
    snapshot: displib.Snapshot, window: Any
) -> None:
    instance, plans = window
    assert snapshot.trains, "trains are running at noon"
    by_id = {t.id: t for t in instance.trains}
    for state in snapshot.trains:
        train = by_id[state.run_id]
        ops = train.operations
        preds = {e.operation for o in ops for e in o.successors}
        assert [o.id for o in ops if o.id not in preds] == [0]  # one entry, where the train is
        for k, occ in enumerate(state.occupations):
            op = ops[k]
            assert op.resources[0].resource == occ.resource
            assert op.start_lb <= occ.since_s <= op.start_ub <= START_S
            if k + 1 < len(state.occupations):
                assert [e.operation for e in op.successors] == [k + 1]  # no alternative behind the head
    entering = [p for p in plans if START_S <= p.entry_s < END_S]
    assert entering and len(plans) > len(entering)


def test_trains_that_left_before_the_window_are_dropped(snapshot: displib.Snapshot, window: Any) -> None:
    instance, _ = window
    ids = {t.id for t in instance.trains}
    assert snapshot.departed and not ids & set(snapshot.departed)


def test_the_evaluator_accepts_the_twin_schedule_for_the_mid_day_window(window: Any, day: Any) -> None:
    instance, _ = window
    result = evaluate(instance, schedule_from_trace(instance, day.occupancy_log))
    assert result.feasible, result.violations[:5]


def test_a_snapshot_must_be_taken_at_the_window_start(world: run.World, snapshot: displib.Snapshot) -> None:
    with pytest.raises(ValueError, match="window start"):
        displib.build_instance(world, DAY, START_S + 600, END_S, snapshot=snapshot)


def test_the_displib_document_follows_the_2025_specification(window: Any) -> None:
    """DISPLIB 2025 §1.2: per train, operations in topological order, the entry at index 0, the exit
    last, successors as forward indices (non-empty except at the exit), non-negative integer times,
    and ``op_delay`` objective components with integer constants."""
    instance, _ = window
    doc = instance.to_displib()
    assert set(doc) == {"trains", "objective"}
    for ops in doc["trains"]:
        targets = {s for op in ops for s in op["successors"]}
        assert 0 not in targets
        assert ops[-1]["successors"] == [] and ops[-1]["resources"] == []
        for i, op in enumerate(ops[:-1]):
            assert op["successors"] and all(isinstance(s, int) and i < s < len(ops) for s in op["successors"])
        for op in ops:
            assert all(
                isinstance(op.get(k, 0), int) and op.get(k, 0) >= 0 for k in ("start_lb", "min_duration")
            )
            assert all(set(r) == {"resource", "release_time"} for r in op["resources"])
        assert set(range(1, len(ops))) <= targets  # every operation but the entry is reachable
    for c in doc["objective"]:
        assert c["type"] == "op_delay"
        assert all(isinstance(c[k], int) and c[k] >= 0 for k in ("train", "operation", "threshold", "coeff"))
        assert 0 <= c["operation"] < len(doc["trains"][c["train"]])
