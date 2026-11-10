"""Gate G3: deterministic sandbox replay of a candidate plan (FR-VER-05, FR-TWIN-16; issue #79)."""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import pytest

from raildss_twin import displib, run, sandbox
from raildss_twin.plans import day_start
from raildss_verifier import network
from raildss_verifier.auditor import plan_from_trace
from raildss_verifier.model import Action, MaintenanceBlock

DAY = dt.date(2025, 8, 20)
START_S = 12 * 3600
HOUR_S = 3600


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def net(world: run.World) -> network.Network:
    return network.load(world.layout.network_version)


@pytest.fixture(scope="module")
def baseline(world: run.World, net: network.Network) -> tuple[Any, Any]:
    """The BL-0 replay of the window and its executed movements (to seed plans from)."""
    _, sim = run.run_day(world, DAY, until_s=START_S + 2 * HOUR_S, keep_events=True)
    executed = plan_from_trace(sim.events, sandbox.train_meta(sim), net, day_start(DAY))
    return sim, executed


def g3(world: run.World, net: network.Network, plan: sandbox.SandboxPlan, **kw: Any) -> sandbox.G3Result:
    return sandbox.replay(world, DAY, START_S, plan, net=net, **kw)


def test_the_bl0_plan_passes_g3(world: run.World, net: network.Network) -> None:
    """The do-nothing BL-0 plan stands in for the greedy plan until the dispatcher (#75) lands."""
    t0 = time.perf_counter()
    result = g3(world, net, sandbox.SandboxPlan(plan_id="bl0"))
    assert result.status in ("PASS", "PASS_WITH_WARN"), result.violations[:3]
    assert result.end_s - result.start_s == 2 * HOUR_S
    assert result.kpis["delta"] == {k: 0.0 for k in result.kpis["plan"]}
    print(f"G3 replay of a 2 h horizon from noon: {time.perf_counter() - t0:.1f} s")


def test_replaying_the_same_plan_from_the_same_fork_is_byte_identical(
    world: run.World, net: network.Network, baseline: tuple[Any, Any]
) -> None:
    _, executed = baseline
    train, call = next(
        (t, c)
        for t in executed.trains
        if t.kind == "freight"
        for c in t.calls[:-1]
        if START_S < c.dep_s < START_S + HOUR_S
    )
    plan = sandbox.SandboxPlan(
        plan_id="hold-one",
        holds=[sandbox.Hold(run_id=train.run_id, station=call.op, until_s=call.dep_s + 600)],
    )
    a, b = g3(world, net, plan), g3(world, net, plan)
    assert a.model_dump_json() == b.model_dump_json()
    assert a.kpis["plan"] != a.kpis["baseline"] or a.violations  # the hold changed the replay


def _quiet_block_run(executed: Any, net: network.Network) -> tuple[Any, int, Any]:
    """A freight train's absolute-block run in the window with no other train on that line nearby."""
    runs = [
        (t, i, r)
        for t in executed.trains
        for i, r in enumerate(t.runs)
        if net.lines[(r.block_id, r.line_id)].system != "AUTO"
    ]
    for t, i, r in runs:
        if t.kind != "freight" or not START_S + 1800 < r.enter_s < START_S + 5400:
            continue
        seg = net.segment(r.block_id, r.line_id)
        busy = [
            o
            for o, _, q in runs
            if o.run_id != t.run_id
            and net.segment(q.block_id, q.line_id) == seg
            and q.enter_s < r.clear_s + HOUR_S
            and q.clear_s > r.enter_s - 600
        ]
        if not busy:
            return t, i, r
    raise AssertionError("no quiet absolute-block run in the window")


def test_a_plan_running_a_train_into_a_maintenance_block_fails_and_holding_it_passes(
    world: run.World, net: network.Network, baseline: tuple[Any, Any]
) -> None:
    _, executed = baseline
    train, i, r = _quiet_block_run(executed, net)
    block = MaintenanceBlock(
        block_id=r.block_id, line_id=r.line_id, start_s=r.enter_s - 60, end_s=r.clear_s + 60
    )
    bad = g3(world, net, sandbox.SandboxPlan(plan_id="into-block", maintenance=[block]))
    assert bad.status == "FAIL"
    assert any(v.rule_id == "VER-MNT-01" and train.run_id in v.trains for v in bad.violations)
    hold = sandbox.Hold(run_id=train.run_id, station=train.calls[i].op, until_s=block.end_s + 60)
    good = g3(world, net, sandbox.SandboxPlan(plan_id="hold-for-block", maintenance=[block], holds=[hold]))
    assert good.status in ("PASS", "PASS_WITH_WARN"), good.violations[:3]


def test_a_planned_line_the_rules_forbid_is_refused_and_fails(
    world: run.World, net: network.Network, baseline: tuple[Any, Any]
) -> None:
    _, executed = baseline
    short = net.tracks[("CPP", 1)]
    assert short.csr_m is not None
    train = next(
        t
        for t in executed.trains
        if t.length_m > short.csr_m and any(c.op == "CPP" and c.arr_s > START_S for c in t.calls)
    )
    line = sandbox.LineChoice(run_id=train.run_id, station="CPP", line_no=1)
    result = g3(world, net, sandbox.SandboxPlan(plan_id="short-loop", lines=[line]))
    assert result.status == "FAIL"
    step = [v for v in result.violations if v.source == "plan_step"]
    assert step and step[0].rule_id in ("VER-XNG-02", "VER-CON-01") and step[0].trains == [train.run_id]


def test_a_plan_with_a_competent_authority_action_fails(world: run.World, net: network.Network) -> None:
    action = Action(run_id="12863", kind="proceed_without_line_clear", time_s=START_S + 600)
    result = g3(world, net, sandbox.SandboxPlan(plan_id="aut", actions=[action]))
    assert result.status == "FAIL" and any(v.rule_id == "VER-AUT-01" for v in result.violations)


def test_the_fork_is_checked_against_a_dss_snapshot(
    world: run.World, net: network.Network, baseline: tuple[Any, Any]
) -> None:
    sim, _ = baseline
    snap = displib.snapshot_from_occupancy(sim.occupancy_log, START_S, world.layout.network_version)
    plan = sandbox.SandboxPlan(plan_id="bl0")
    assert g3(world, net, plan, snapshot=snap).snapshot_mismatch == []
    first = snap.trains[0]
    wrong = first.model_copy(
        update={"occupations": [displib.Occupation(resource="SL:XXX/9", since_s=START_S)]}
    )
    moved = snap.model_copy(update={"trains": [wrong, *snap.trains[1:]]})
    assert g3(world, net, plan, snapshot=moved).snapshot_mismatch == [first.run_id]
