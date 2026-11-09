"""Rules the movement simulator enforces (FR-TWIN-10; issues #51, #101)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_twin import run
from raildss_twin.sim import ENFORCED_RULES

DAY = dt.date(2025, 8, 20)


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def day_run(world: run.World) -> tuple[Any, Any]:
    return run.run_day(world, DAY)


def _csr(world: run.World, op: str, line_no: int) -> float | None:
    return next(sl.csr_m for sl in world.layout.station_lines[op] if sl.line_no == line_no)


def test_a_train_longer_than_a_loop_is_refused_there_and_routed_elsewhere(world: run.World) -> None:
    plans, _, _ = run.plans_for(world, DAY)
    plan = max((p for p in plans if p.kind == "freight"), key=lambda p: (p.template.length_m, p.run_id))
    report, sim = run.run_day(world, DAY, plans=[plan])
    assert report.completed
    assert sim.refusals, "the longest freight train meets at least one short loop"
    taken = {label for _, label, rid, occupied in sim.occupancy_log if occupied and rid == plan.run_id}
    for r in sim.refusals:
        csr = _csr(world, r.station, r.line)
        assert csr is not None and csr < plan.template.length_m
        assert r.rule == "VER-XNG-02" and f"CSR {csr:.0f} m" in r.reason and r.run_id == plan.run_id
        assert f"SL:{r.station}/{r.line}" not in taken
        assert any(label.startswith(f"SL:{r.station}/") for label in taken)  # received on another line


def test_no_train_is_ever_received_on_a_station_line_shorter_than_itself(
    world: run.World, day_run: tuple[Any, Any]
) -> None:
    report, sim = day_run
    assert report.completed and report.csr_refused == len(sim.refusals) > 0
    length = {t.plan.run_id: t.plan.template.length_m for t in sim.trains}
    short = []
    for time_s, label, run_id, occupied in sim.occupancy_log:
        if occupied and label.startswith("SL:"):
            op, _, no = label.removeprefix("SL:").rpartition("/")
            csr = _csr(world, op, int(no))
            if csr is not None and csr < length[run_id]:
                short.append((time_s, run_id, label))
    assert short == []


def test_the_report_lists_the_enforced_rules_including_the_direction_gate(day_run: tuple[Any, Any]) -> None:
    report, _ = day_run
    assert report.rules == sorted(ENFORCED_RULES)
    assert {"VER-BLK-01", "VER-BLK-02", "VER-BLK-04", "VER-HDW-01", "VER-CON-01", "VER-XNG-02"} <= set(
        report.rules
    )
