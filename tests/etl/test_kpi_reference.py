"""Historical reference KPI tests (issue #80, FR-KPI-11, BL-5)."""

from __future__ import annotations

from typing import Any

import pytest

from raildss_etl import freight, kpi_reference, publish, topology
from raildss_etl.freight import Stage6Report
from raildss_etl.load import run_stage1
from raildss_etl.mapping import load_mapping
from raildss_params import load

VERSION = "KTV-PSA-2025-08"


@pytest.fixture(scope="module")
def built() -> tuple[dict[str, Any], Stage6Report]:
    spec, reg = load_mapping(), load()
    tables, _ = run_stage1(spec, write=False)
    topo, _ = topology.build_topology(tables, spec, reg)
    demand, stage6 = freight.build_freight(tables, topo, spec, reg)
    return kpi_reference.build_reference(tables, topo, demand, stage6, spec, reg), stage6


def test_committed_report_is_reproduced_from_the_log(built: Any) -> None:
    assert kpi_reference.diff(built[0]) == []


def test_month_figures_equal_etl_stage_6(built: Any) -> None:
    ref, s6 = built
    assert ref["loads"] == s6.loads
    transit = ref["through_transit_h"]["month"]
    assert transit == s6.through_transit_h
    assert ref["loads_per_day"]["loads_per_day_by_block"] == s6.loads_per_day_by_block
    assert ref["loads_per_day"]["busiest_block_loads_per_day"] == s6.busiest_block_loads_per_day
    for station, stats in s6.dwell_min.items():
        got = ref["dwell_min"]["month"][station]
        assert (got["median"], got["mean"], got["p90"], got["n"]) == (
            stats["median"],
            stats["mean"],
            stats["p90"],
            stats["n"],
        )
        assert got["share_over_long"] == stats["share_over_30"]
    assert ref["delay_indicators"]["month"]["stabled_loads"] == s6.stabled_loads


def test_values_reproduce_r06(built: Any) -> None:
    """R06 §2: transit median 10.4 h (p10 about 6.5, p90 about 17.4); VZM dwell 3 / 32.1 / 95 / 29.7 %."""
    t = built[0]["through_transit_h"]["month"]
    assert abs(t["median"] - 10.4) <= 0.1 and abs(t["p10"] - 6.5) <= 0.2 and abs(t["p90"] - 17.4) <= 0.7
    vzm = built[0]["dwell_min"]["month"]["VZM"]
    assert (vzm["median"], vzm["mean"], vzm["p90"]) == (3.0, 32.1, 95.0)
    assert round(vzm["share_over_long"], 3) == 0.297
    busiest = built[0]["loads_per_day"]["busiest_block_loads_per_day"]
    assert (
        abs(busiest["ktv_vzm/DN"] - 39.5) <= 0.05 * 39.5 + 1 and abs(busiest["vzm_psa/UP"] - 15) <= 0.07 * 15
    )


def test_every_day_is_reported_and_days_add_up_to_the_month(built: Any) -> None:
    ref, _ = built
    assert len(ref["days"]) == 27 and set(ref["loads_by_day"]) == set(ref["days"])
    assert sum(ref["loads_by_day"].values()) == ref["loads"]
    assert (
        sum(v["n"] for v in ref["through_transit_h"]["by_day"].values())
        == ref["through_transit_h"]["month"]["n"]
    )
    by_day = ref["delay_indicators"]["by_day"]
    assert set(ref["days"]) <= set(by_day)
    month = ref["delay_indicators"]["month"]
    assert sum(v["detained_calls"] for v in by_day.values()) == month["detained_calls"]
    assert sum(v["stabled_loads"] for v in by_day.values()) == month["stabled_loads"]
    for day in ref["days"]:
        assert ref["loads_per_day"]["busiest_block_by_day"]["vzm_psa/DN"].get(day, 0) >= 0


def test_report_agrees_with_the_published_network(built: Any) -> None:
    """The publication carries the loads and the day instances; the report must cover the same ones."""
    ref, _ = built
    _, tables, _ = publish.read_export(publish.default_dir(VERSION))
    assert ref["network_version"] == VERSION
    assert ref["loads"] == len(tables["freight_load"])
    assert ref["loads_by_day"] == {str(d.day): len(d.run_ids) for d in tables["day_instance"]}  # type: ignore[attr-defined]
    stabled = sum(1 for ld in tables["freight_load"] if ld.stabled)  # type: ignore[attr-defined]
    assert ref["delay_indicators"]["month"]["stabled_loads"] == stabled


def test_the_markdown_has_month_and_per_day_tables(built: Any) -> None:
    md = kpi_reference.to_markdown(built[0])
    assert "## Month" in md and "## Per day" in md and "2025-08-27" in md
    assert md.count("\n| 2025-08-") == 27
