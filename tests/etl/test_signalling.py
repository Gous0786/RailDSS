"""KTV-VZM sub-blocks, twin signals and station routes (issue #13 acceptance criteria)."""

from __future__ import annotations

import pandas as pd
import pytest

from raildss_etl.load import run_stage1
from raildss_etl.signalling import build_signalling, validate
from raildss_etl.stations import build_station_lines
from raildss_etl.topology import Topology, build_topology
from raildss_schema.entities import StationLine


@pytest.fixture(scope="module")
def inputs() -> tuple[Topology, list[StationLine]]:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    return build_topology(tables)[0], build_station_lines(tables)[0]


def test_default_spacing_tiles_and_validates(inputs: tuple[Topology, list[StationLine]]) -> None:
    topo, lines = inputs
    sig = build_signalling(topo, lines)
    assert sig.spacing_km == 1.0
    assert validate(sig, topo) == []
    auto_lines = {(ln.block_section_id, ln.line_id) for ln in topo.block_section_lines if ln.system == "AUTO"}
    assert len(auto_lines) == 24  # 4 KTV-VZM blocks x 2 directions x 3 lines
    assert {(s.block_section_id, s.line_id) for s in sig.sub_blocks} == auto_lines
    assert all(s.source == "synthetic" for s in sig.signals)
    kuk_vzm_dn = [s for s in sig.sub_blocks if (s.block_section_id, s.line_id) == ("KUK-VZM", "DN")]
    assert len(kuk_vzm_dn) == 11  # 10.65 km / ~1.0 km


@pytest.mark.parametrize("spacing", [1.0, 1.25, 1.5])
def test_sensitivity_variants_build(inputs: tuple[Topology, list[StationLine]], spacing: float) -> None:
    topo, lines = inputs
    sig = build_signalling(topo, lines, spacing_km=spacing)
    assert validate(sig, topo) == []
    sizes = [s.to_offset_m - s.from_offset_m for s in sig.sub_blocks]
    assert all(abs(x / 1000 - spacing) <= spacing / 2 for x in sizes)


def test_validator_detects_gap_and_overlap(inputs: tuple[Topology, list[StationLine]]) -> None:
    topo, lines = inputs
    sig = build_signalling(topo, lines)
    first = sig.sub_blocks[1]
    gap = sig.model_copy(
        update={
            "sub_blocks": [
                first.model_copy(update={"from_offset_m": first.from_offset_m + 50}) if s is first else s
                for s in sig.sub_blocks
            ]
        }
    )
    assert any("gap" in p for p in validate(gap, topo))
    over = sig.model_copy(
        update={
            "sub_blocks": [
                first.model_copy(update={"from_offset_m": first.from_offset_m - 50}) if s is first else s
                for s in sig.sub_blocks
            ]
        }
    )
    assert any("overlap" in p for p in validate(over, topo))


def test_every_connection_edge_has_a_route(inputs: tuple[Topology, list[StationLine]]) -> None:
    topo, lines = inputs
    sig = build_signalling(topo, lines)
    assert len(sig.routes) == len(topo.connection_edges) == 392
    assert all(r.overlap_m == 180.0 for r in sig.routes)
