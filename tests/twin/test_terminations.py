"""Short-termination and cancellation of a train by a directive (issue #156, FR-OPT-11)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_twin import run, sandbox
from raildss_twin.kpi_eval import run_arm
from raildss_twin.sim import Directives

DAY = dt.date(2025, 8, 20)
T0 = 10 * 3600.0
END = T0 + 2 * 3600.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def _passenger(world: run.World, *, entered: bool) -> tuple[str, str]:
    """(run id, a booked intermediate stop) of a passenger train in the section at T0, or entering after."""
    plans, _, _ = run.plans_for(world, DAY)
    for p in sorted(plans, key=lambda p: p.entry_s):
        if p.kind != "passenger" or not p.timetabled:
            continue
        stops = [n for n in p.nodes[1:-1] if n.stop and not world.layout.ops[n.op].is_halt]
        inside = p.entry_s < T0 and any(n.arr > T0 + 600 for n in stops)
        later = T0 + 600 < p.entry_s < END - 3600
        if stops and (inside if entered else later):
            stop = next(n for n in stops if n.arr > T0 + 600) if entered else p.nodes[0]
            return p.run_id, stop.op
    raise AssertionError("no passenger train")


def _ops(sim: Any, rid: str) -> list[str]:
    return [lab.removeprefix("SL:").split("/")[0] for _, lab, r, occ in sim.occupancy_log if r == rid and occ]


def test_a_short_terminated_train_ends_its_run_at_the_station(world: run.World) -> None:
    rid, station = _passenger(world, entered=True)
    rep, sim = run.run_day(
        world, DAY, until_s=END, directives=Directives(from_s=T0, terminations={rid: station})
    )
    stations = [s for s in _ops(sim, rid) if not s.startswith(("BL:", "SB:"))]
    assert stations[-1] == station  # it went no further
    train = next(t for t in sim.trains if t.plan.run_id == rid)
    assert train.terminated and train.exit_s is None and rep.terminated == 1
    base = run_arm(world, DAY, "BL-0", "t")
    ended = run_arm(world, DAY, "X", "t", directives=Directives(from_s=T0, terminations={rid: station}))
    assert ended.kpis["B1_trains_exited"] == base.kpis["B1_trains_exited"] - 1  # it did not leave the section


def test_a_cancelled_train_does_not_enter(world: run.World) -> None:
    rid, first = _passenger(world, entered=False)
    _, sim = run.run_day(world, DAY, until_s=END, directives=Directives(from_s=T0, terminations={rid: first}))
    train = next(t for t in sim.trains if t.plan.run_id == rid)
    assert train.terminated and not train.entered and _ops(sim, rid) == []


def test_the_g3_replay_applies_the_terminations_and_the_known_incidents(world: run.World) -> None:
    rid, station = _passenger(world, entered=True)
    plan = sandbox.SandboxPlan(plan_id="t", terminations={rid: station})
    res = sandbox.replay(world, DAY, T0, plan, register=world.register)
    assert res.kpis["plan"]["trains_exited"] <= res.kpis["baseline"]["trains_exited"]
    assert not [v for v in res.violations if rid in v.trains]  # a short-termination is not a violation
