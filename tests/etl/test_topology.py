"""ETL stage 2 topology tests (issue #7 acceptance criteria)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

from raildss_etl.load import run_stage1
from raildss_etl.topology import Stage2Report, Topology, build_topology, write_topology

HALTS = {"BTVA", "HCM", "DGB", "RMZ"}


@pytest.fixture(scope="module")
def tables() -> dict[str, pd.DataFrame]:
    return run_stage1(write=False)[0]


@pytest.fixture(scope="module")
def built(tables: dict[str, pd.DataFrame]) -> tuple[Topology, Stage2Report]:
    return build_topology(tables)


def test_report_is_clean(built: tuple[Topology, Stage2Report]) -> None:
    assert built[1].ok


def test_distances_dq15(built: tuple[Topology, Stage2Report]) -> None:
    rep = built[1]
    assert Decimal(rep.route_sum_km) == Decimal(rep.route_cum_km) == Decimal("176.83")
    assert Decimal(rep.reverse_sum_km) == Decimal("176.81")
    assert rep.reverse_diff_km <= rep.reverse_tolerance_km


def test_operational_points(built: tuple[Topology, Stage2Report]) -> None:
    ops = built[0].operational_points
    assert len(ops) == 22
    assert [o.code for o in ops][:2] == ["KTV", "KPL"] and ops[-1].code == "PSA"
    assert {o.code for o in ops if o.is_halt} == HALTS
    assert {o.code for o in ops if o.type == "halt"} == HALTS
    assert all(o.km_ref_hwh is not None for o in ops)
    kms = [o.km_ref_hwh for o in ops]
    assert kms == sorted(kms, reverse=True)  # chainage from Howrah falls along KTV -> PSA
    psa = ops[-1]
    assert psa.km == 176.83 and psa.is_boundary and psa.crew_change


def test_block_sections_both_levels(built: tuple[Topology, Stage2Report]) -> None:
    topo, rep = built
    assert rep.coa_blocks == 34 and rep.route_split_blocks == 16
    assert rep.route_blocks_missing == []
    assert rep.empty_rows_dropped["BLOCKSCTN"] == ["PSA-KTV"]
    cpp = next(b for b in topo.block_sections if b.id == "CPP-SGDM")
    assert (cpp.level, cpp.length_m, cpp.system, cpp.n_lines) == ("coa", 13270.0, "ABS_absolute", 2)


def test_halts_are_stopping_points_inside_coa_blocks(built: tuple[Topology, Stage2Report]) -> None:
    stops = built[0].stopping_points
    assert {p.op_code for pts in stops.values() for p in pts} == HALTS
    assert [(p.op_code, p.offset_m) for p in stops["CPP-SGDM"]] == [("BTVA", 4430.0)]
    assert len(stops) == 8  # 4 halts x 2 directions


def test_regimes_and_lines(built: tuple[Topology, Stage2Report]) -> None:
    topo, rep = built
    assert rep.coa_blocks_without_lines == [] and rep.regime_violations == []
    kuk_vzm = sorted(ln.line_id for ln in topo.block_section_lines if ln.block_section_id == "KUK-VZM")
    assert kuk_vzm == ["DN", "MID", "UP"]
    assert {ln.system for ln in topo.block_section_lines if ln.block_section_id == "KUK-VZM"} == {"AUTO"}
    assert {ln.direction for ln in topo.block_section_lines if ln.line_id == "MID"} == {"BOTH"}


def test_connections_resolve(built: tuple[Topology, Stage2Report]) -> None:
    topo, rep = built
    assert rep.unresolved_connections == [] and rep.connection_edges == 392
    assert {e.mode for e in topo.connection_edges} == {"receive", "send"}
    assert rep.connection_edges_to_boundary > 0
    assert set(topo.boundary_blocks) >= {"KTV-PDT", "VZM-GTLM", "NWP-TEK", "PSA-SUDV"}


def test_build_is_deterministic_and_written(
    tables: dict[str, pd.DataFrame], built: tuple[Topology, Stage2Report], tmp_path: Path
) -> None:
    assert build_topology(tables)[0].content_hash() == built[0].content_hash()
    write_topology(*built, tmp_path)
    assert (tmp_path / "topology_report.json").exists()
    assert len((tmp_path / "operational_points.jsonl").read_text(encoding="utf-8").splitlines()) == 22


@pytest.mark.parametrize(("table", "block"), [("BLOCKSCTN", "KUK-VZM"), ("BLOCKSCTNLINE", "KUK-VZM")])
@pytest.mark.parametrize("blank", ["", "NA", "0"])
def test_missing_max_speed_is_an_error(
    tables: dict[str, pd.DataFrame], table: str, block: str, blank: str
) -> None:
    broken = dict(tables)
    df = tables[table].copy()
    df.loc[df["MAVBLCKSCTN"] == block, "MANMAXSPEED"] = blank
    broken[table] = df
    with pytest.raises(ValueError, match=f"{table} {block}.*MANMAXSPEED"):
        build_topology(broken)
