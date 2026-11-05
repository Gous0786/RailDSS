"""ETL stage 4 tests: PSRs and line speeds (issue #9 acceptance criteria)."""

from __future__ import annotations

import copy

import pandas as pd
import pytest

from raildss_etl.load import run_stage1
from raildss_etl.speeds import Speeds, Stage4Report, build_speeds
from raildss_etl.stations import build_station_lines
from raildss_etl.topology import Topology, build_topology
from raildss_params import load

Ctx = tuple[dict[str, pd.DataFrame], Topology, list, Speeds, Stage4Report]  # type: ignore[type-arg]


@pytest.fixture(scope="module")
def ctx() -> Ctx:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    topo, _ = build_topology(tables)
    lines, _, _ = build_station_lines(tables)
    speeds, rep = build_speeds(tables, topo, lines)
    return tables, topo, lines, speeds, rep


def test_all_eight_psrs_map_to_the_right_direction(ctx: Ctx) -> None:
    _, topo, _, speeds, rep = ctx
    assert rep.ok, rep.problems
    psrs = speeds.speed_profiles
    assert [(p.direction, p.kind) for p in psrs].count(("DN", "PSR")) == 5
    assert [(p.direction, p.kind) for p in psrs].count(("UP", "PSR")) == 3
    blocks = {b.id: b for b in topo.block_sections if b.level == "coa"}
    lines = {(x.block_section_id, x.line_id): x for x in topo.block_section_lines}
    placed = {pl.id: pl for pl in rep.placements}
    assert set(placed) == {p.id for p in psrs}
    for p in psrs:
        pl = placed[p.id]
        assert p.block_section_id in blocks and blocks[p.block_section_id].direction == p.direction
        assert pl.lines and all((p.block_section_id, lid) in lines for lid in pl.lines)
        assert all(lines[(p.block_section_id, lid)].direction in (p.direction, "BOTH") for lid in pl.lines)
        length = blocks[p.block_section_id].length_m
        for loc in (p.location_from, p.location_to):
            assert loc.ne_id == p.block_section_id and loc.km is not None
            assert loc.offset_m is not None and 0 <= loc.offset_m <= length


def test_placement_values(ctx: Ctx) -> None:
    by_id = {p.id: p for p in ctx[3].speed_profiles}
    p = by_id["5442"]  # PDU-DUSI, km 761 mast 0, 3000 m, falling chainage in the DN direction
    assert (p.location_from.km, p.location_to.km, p.length_m) == (761.0, 758.0, 3000.0)
    assert (p.vmax_passenger_kmh, p.vmax_goods_kmh, p.stop_dead) == (90.0, 90.0, False)
    assert (p.time_loss_passenger_s, p.time_loss_goods_s) == (27.0, 54.0)
    up = by_id["29738"]  # TIU-ULM, rising chainage, km post flag K
    assert up.location_from.km == 734.54 and up.location_to.km > up.location_from.km
    assert up.source == "data" and up.raw["MACKMOHEFLAG"] == "K"
    assert by_id["5440"].line_id is None  # DN and MID both carry DN traffic


def test_kuk_vzm_yard_overshoot_is_reported_not_hidden(ctx: Ctx) -> None:
    pl = next(x for x in ctx[4].placements if x.id == "5440")
    assert pl.outside_block_m > 0
    assert pl.outside_block_m <= float(load().value("psr.block_end_tolerance_m"))


def test_every_line_has_a_speed_and_none_exceeds_the_maximum(ctx: Ctx) -> None:
    topo, _, speeds, rep = ctx[1], None, ctx[3], ctx[4]
    assert len(speeds.station_lines) == 103 and all(x.line_speed_kmh for x in speeds.station_lines)
    assert all(0 < x.vmax_kmh <= 130 for x in topo.block_section_lines)
    assert rep.max_speed_kmh <= rep.sectional_max_kmh == 130
    assert rep.assigned_station_lines == ["NWP/1 (siding)", "PDU/2 (siding)", "PDU/5 (siding)"]
    kinds = {(x.kind, x.line_speed_kmh) for x in speeds.station_lines}
    assert kinds == {("loop", 15.0), ("main", 100.0), ("main", 110.0), ("siding", 15.0)}


@pytest.mark.parametrize(
    ("column", "value", "message"),
    [
        ("MACKMOHEFLAG", "X", "unknown MACKMOHEFLAG"),
        ("MAVFROMSUBKM", "", "reference is missing"),
        ("MAVFROMSUBKM", "abc", "not numeric"),
        ("MAVFROMSUBKM", "400", "does not fit within km"),
        ("MANDISTANCE", "", "restricted length"),
        ("MAVBLCKSCTN", "NML-VZM", "runs UP"),
        ("MAVBLCKSCTN", "XXX-YYY", "not a COA block section"),
        ("MANFROMKM", "700", "outside block"),
    ],
)
def test_unresolved_reference_fails_the_stage(ctx: Ctx, column: str, value: str, message: str) -> None:
    tables = copy.deepcopy(ctx[0])
    tables["PSR_DN"].loc[tables["PSR_DN"].index[0], column] = value
    _, rep = build_speeds(tables, ctx[1], ctx[2])
    assert not rep.ok
    assert any("PSR 5440" in p and message in p for p in rep.problems), rep.problems
    assert len(rep.placements) == 7  # the bad PSR is not placed approximately


def test_goods_speed_above_the_line_speed_fails(ctx: Ctx) -> None:
    tables = copy.deepcopy(ctx[0])
    tables["PSR_DN"].loc[tables["PSR_DN"].index[1], "MANGDSSPEED"] = (
        "125"  # VZM-NML DN line is 130; use a slower one
    )
    topo = ctx[1].model_copy(
        update={
            "block_section_lines": [
                x.model_copy(update={"vmax_kmh": 100.0}) if x.block_section_id == "VZM-NML" else x
                for x in ctx[1].block_section_lines
            ]
        }
    )
    _, rep = build_speeds(tables, topo, ctx[2])
    assert any("PSR 5441" in p and "goods 125" in p and "above the line speed" in p for p in rep.problems)


def test_missing_main_line_speed_fails_instead_of_defaulting(ctx: Ctx) -> None:
    main = next(x for x in ctx[2] if x.kind == "main")
    lines = [x.model_copy(update={"line_speed_kmh": None}) if x is main else x for x in ctx[2]]
    _, rep = build_speeds(ctx[0], ctx[1], lines)
    assert not rep.ok and any(f"{main.op_code}/{main.line_no}" in p and "no speed" in p for p in rep.problems)
    assert f"{main.op_code}/{main.line_no} (main)" not in rep.assigned_station_lines
