"""Line blocks, single-line working and direction gates in the IR (issue #153)."""

from __future__ import annotations

import datetime as dt

import pytest

from raildss_twin import displib, run
from raildss_twin.injector import Disruption
from raildss_twin.sim import Closure

DAY = dt.date(2025, 8, 7)
BLOCK_S = 10 * 3600.0
T0, END = 8 * 3600.0, 10.5 * 3600.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def slw(world: run.World) -> displib.Instance:
    block = Disruption(
        incident_id="B",
        kind="line_block",
        start_s=BLOCK_S,
        duration_s=7200,
        block_id="CPP-SGDM",
        line_id="DN",
        single_line_working=True,
    )
    return displib.build_instance(world, DAY, T0, END, incidents=[block])[0]


def test_a_line_block_makes_its_line_unavailable(slw: displib.Instance) -> None:
    assert [(u.resource, u.start_s, u.end_s) for u in slw.unavailable] == [
        ("BL:CPP~SGDM/DN", int(BLOCK_S), int(BLOCK_S + 7200))
    ]
    assert slw.to_displib()["unavailable"][0]["resource"] == "BL:CPP~SGDM/DN"


def test_single_line_working_lets_dn_trains_take_the_up_line_while_it_lasts(
    world: run.World, slw: displib.Instance
) -> None:
    borrowed = [
        o for t in slw.trains for o in t.operations if o.block == "CPP-SGDM" and o.line == "UP"
    ]  # the DN record on the UP line: only under single-line working
    assert borrowed
    assert all(o.start_lb >= BLOCK_S and o.start_ub == BLOCK_S + 7200 for o in borrowed)
    plain = displib.build_instance(world, DAY, T0, END)[0]
    assert not [o for t in plain.trains for o in t.operations if o.block == "CPP-SGDM" and o.line == "UP"]
    assert not plain.unavailable


def test_a_closure_is_unavailable_and_reversible_lines_carry_a_direction_gate(world: run.World) -> None:
    closure = Closure("ALM~KPL", "DN", BLOCK_S, BLOCK_S + 3600)
    inst = displib.build_instance(world, DAY, T0, END, closures=[closure])[0]
    assert {u.resource for u in inst.unavailable} == {
        f"SB:{k}" for k, _ in world.layout.canonical_sub_blocks("KPL-ALM", "DN")
    }
    gated = [o for t in inst.trains for o in t.operations if o.gate]
    assert gated and all(o.line == "MID" and o.direction in ("DN", "UP") for o in gated)
    assert {o.direction for o in gated} == {"DN", "UP"}  # both directions use the MID line
    doc = inst.to_displib()
    assert any(op.get("gate") for ops in doc["trains"] for op in ops)
