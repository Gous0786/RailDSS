"""DISPLIB-style converter tests (issue #22 acceptance criteria).

The window is the first two hours of the real day 20 Aug 2025. No DISPLIB instance, solution or
verifier is used or committed (licence unstated, A-07); the checks run on our own generated IR.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from raildss_twin import displib, run
from raildss_twin.displib_check import evaluate, schedule_from_trace, serial_schedule

DAY = dt.date(2025, 8, 20)
WINDOW_S = 2 * 3600
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def window(world: run.World) -> tuple[displib.Instance, list[Any]]:
    return displib.build_instance(world, DAY, 0, WINDOW_S)


def test_two_hour_window_converts_and_reports_counts(window: Any) -> None:
    instance, plans = window
    counts = instance.counts
    assert counts["trains"] == len(plans) > 10
    assert (
        counts["resources"] > 100 and counts["operations"] > 1000 and counts["edges"] > counts["operations"]
    )
    assert counts["objective_components"] > counts["trains"]
    assert instance.network_version == "KTV-PSA-2025-08"
    assert instance.window.day == DAY and instance.window.end_s == WINDOW_S
    assert {r.kind for r in instance.resources} == {"station_line", "block_line", "sub_block"}
    # every train enters in the window
    assert all(0 <= p.entry_s < WINDOW_S for p in plans)


def test_ir_json_round_trip_is_lossless(window: Any) -> None:
    instance, _ = window
    text = instance.to_json()
    again = displib.Instance.from_json(text)
    assert again == instance
    assert again.to_json() == text


def test_conversion_is_deterministic(world: run.World, window: Any) -> None:
    instance, _ = window
    again, _ = displib.build_instance(world, DAY, 0, WINDOW_S)
    assert again.to_json() == instance.to_json()


def test_displib_structure(window: Any) -> None:
    instance, _ = window
    doc = instance.to_displib()
    assert set(doc) == {"trains", "objective"}
    assert len(doc["trains"]) == len(instance.trains)
    op = doc["trains"][0][0]
    assert set(op) == {"start_lb", "start_ub", "min_duration", "resources", "successors"}
    assert all(isinstance(op[k], int) for k in ("start_lb", "start_ub", "min_duration"))
    json.dumps(doc)  # plain JSON


def test_edges_follow_connections_only(world: run.World, window: Any) -> None:
    """Station-to-block edges exist only where Connections says send; block-to-station, receive."""
    instance, _ = window
    layout = world.layout
    checked = 0
    for train in instance.trains:
        ops = {o.id: o for o in train.operations}
        for o in train.operations:
            for s in o.successors:
                nxt = ops[s.operation]
                if o.kind == "station" and nxt.kind != "station":
                    sl = int(o.resources[0].resource.split("/")[-1])
                    assert (nxt.block, nxt.line) in layout.send[(o.node, sl)]
                    checked += 1
                elif o.kind != "station" and nxt.kind == "station":
                    sl = int(nxt.resources[0].resource.split("/")[-1])
                    assert (o.block, o.line) in layout.receive[(nxt.node, sl)]
                    checked += 1
    assert checked > 500


def test_every_operation_lies_on_a_path_and_bounds_are_ordered(window: Any) -> None:
    instance, _ = window
    for train in instance.trains:
        preds = {s.operation for o in train.operations for s in o.successors}
        roots = [o for o in train.operations if o.id not in preds]
        ends = [o for o in train.operations if not o.successors]
        assert roots and ends and all(r.kind == "station" for r in [*roots, *ends])
        assert all(0 <= o.start_lb <= o.start_ub and o.min_duration >= 0 for o in train.operations)
        assert all(o.resources for o in train.operations)


def test_a_trivially_feasible_schedule_is_accepted(world: run.World) -> None:
    small, _ = displib.build_instance(world, DAY, 0, 1800)
    schedule = serial_schedule(small)
    result = evaluate(small, schedule)
    assert result.feasible, result.violations[:3]
    assert result.objective > 0


def test_the_bl0_twin_output_is_feasible_for_the_converted_window(world: run.World, window: Any) -> None:
    instance, plans = window
    report, sim = run.run_day(world, DAY, plans=plans)
    assert report.completed
    schedule = schedule_from_trace(instance, sim.occupancy_log)
    result = evaluate(instance, schedule)
    assert result.feasible, result.violations[:5]
    serial = evaluate(
        instance, serial_schedule(instance), tolerance_s=1e9
    )  # bounds ignored: a yardstick only
    assert 0 < result.objective < serial.objective


def test_the_evaluator_catches_violations(world: run.World) -> None:
    small, _ = displib.build_instance(world, DAY, 0, 1800)
    good = serial_schedule(small)
    assert evaluate(small, good).feasible
    # 1. a start before the release time
    early = {**good, 0: [(o, s - 100_000) for o, s in good[0]]}
    assert any("outside" in v for v in evaluate(small, early).violations)
    # 2. a successor that starts before the minimum duration has passed
    ops = {o.id: o for o in small.trains[0].operations}
    n = next(i for i, (oid, _) in enumerate(good[0][:-1]) if ops[oid].min_duration > 0)
    rushed = {**good, 0: [*good[0][: n + 1], (good[0][n + 1][0], good[0][n][1]), *good[0][n + 2 :]]}
    assert any("before" in v for v in evaluate(small, rushed).violations)
    # 3. a path that does not follow an edge
    broken = {**good, 0: [good[0][0], good[0][-1]]}
    assert any("no edge" in v or "successors" in v for v in evaluate(small, broken).violations)
    # 4. two trains on one resource at once
    twin = small.model_copy(
        update={"trains": [small.trains[0], small.trains[0].model_copy(update={"id": "COPY"})]}
    )
    both = {0: good[0], 1: good[0]}
    assert any("holds" in v for v in evaluate(twin, both).violations)
    # 5. a missing schedule
    assert any("no schedule" in v for v in evaluate(small, {}).violations)


def test_no_displib_files_are_in_the_repository() -> None:
    suspects = [
        p
        for p in (ROOT / "data").rglob("*")
        if p.is_file() and "displib" in p.name.lower() and "interim" not in p.relative_to(ROOT).parts
    ]
    assert suspects == []
    assert not list(ROOT.glob("displib_verify*")) and not list(ROOT.glob("tests/**/displib_verify*"))
