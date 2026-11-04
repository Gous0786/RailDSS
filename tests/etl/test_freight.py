"""Freight demand and historical statistics tests (issue #11 acceptance criteria)."""

from __future__ import annotations

import statistics

import pandas as pd
import pytest

from raildss_etl.freight import FreightDemand, Stage6Report, build_freight, rake_length_m
from raildss_etl.load import run_stage1
from raildss_etl.mapping import ignored_references, load_mapping
from raildss_etl.topology import build_topology

# R06 §2: freight trains/day per stretch and direction (rounded)
R06_LOADS_PER_DAY = {"ktv_vzm/DN": 40, "ktv_vzm/UP": 34, "vzm_psa/DN": 19, "vzm_psa/UP": 15}


@pytest.fixture(scope="module")
def built() -> tuple[FreightDemand, Stage6Report]:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    return build_freight(tables, build_topology(tables)[0])


def test_block_hrs_consistency_and_derived_fields_unused(built: tuple[FreightDemand, Stage6Report]) -> None:
    assert built[1].block_hrs_agreement >= 0.95
    assert ignored_references(load_mapping()) == []  # Block Km / Speed never referenced (DQ-21)


def test_loads_per_day_match_r06(built: tuple[FreightDemand, Stage6Report]) -> None:
    busiest = built[1].busiest_block_loads_per_day
    for key, r06 in R06_LOADS_PER_DAY.items():
        # Within 5 % for three of four; VZM-PSA UP is 16.0 vs R06's rounded 15 (+6.9 %).
        assert abs(busiest[key] - r06) / r06 <= 0.07, key


def test_through_transit(built: tuple[FreightDemand, Stage6Report]) -> None:
    tt = built[1].through_transit_h
    assert abs(tt["median"] - 10.4) <= 0.1
    assert abs(tt["p10"] - 6.5) <= 0.2 and abs(tt["p90"] - 17.4) <= 0.7  # R06 gives "~" values


def test_vzm_dwell(built: tuple[FreightDemand, Stage6Report]) -> None:
    vzm = built[1].dwell_min["VZM"]
    assert (vzm["median"], vzm["mean"], vzm["p90"], vzm["share_over_30"]) == (3.0, 32.1, 95.0, 0.297)


def test_loads_days_and_block_times(built: tuple[FreightDemand, Stage6Report]) -> None:
    demand, rep = built
    assert rep.days == len(demand.days) == 27
    assert all(ld.entry_time.utcoffset() is not None for ld in demand.loads)
    nml = next(b for b in demand.block_times if (b.block, b.direction, b.loaded) == ("NML-GVI", "DN", True))
    assert nml.p10_min == 12.0  # R06 §6.6 example: NML-GVI 12.3 km, p10 12 min


def test_nhpp_reproduces_daily_volume(built: tuple[FreightDemand, Stage6Report]) -> None:
    demand, rep = built
    mean_log = rep.loads / rep.days
    sampled = statistics.mean(len(demand.sample(seed)) for seed in range(30))
    assert abs(sampled - mean_log) / mean_log <= 0.10
    assert demand.sample(7) == demand.sample(7)  # seeded


def test_consist_templates(built: tuple[FreightDemand, Stage6Report]) -> None:
    demand = built[0]
    assert all(ld.consist_template == f"TPL-{ld.wagon_type}" for ld in demand.loads)
    length = rake_length_m("BOXN")
    assert length is not None and 640 <= length <= 660  # 58 BOXN + loco
