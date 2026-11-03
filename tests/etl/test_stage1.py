"""ETL stage 1 tests (issue #6 acceptance criteria)."""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from raildss_etl.load import NEGATIVE_DWELL, SECONDS_PER_DAY, Stage1Report, run_stage1
from raildss_etl.mapping import REPO

_SPEC = importlib.util.spec_from_file_location("manifest", REPO / "tools" / "manifest.py")
assert _SPEC and _SPEC.loader
manifest = importlib.util.module_from_spec(_SPEC)
sys.modules["manifest"] = manifest
_SPEC.loader.exec_module(manifest)

EXPECTED = {
    "STATION": 22,
    "BLOCKSCTN": 63,
    "STTNLINE": 103,
    "PLATFORM": 70,
    "CONNECTIONS": 392,
    "TRAIN": 250,
    "SCHEDULE": 4260,
    "BLOCKSCTNLINE": 99,
    "FREIGHT": 95214,
}


@pytest.fixture(scope="module")
def stage1(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict[str, pd.DataFrame], Stage1Report, Path]:
    out = tmp_path_factory.mktemp("interim")
    tables, report = run_stage1(out_dir=out)
    return tables, report, out


def test_row_counts_after_cleaning(stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path]) -> None:
    tables, report, _ = stage1
    for name, n in EXPECTED.items():
        assert len(tables[name]) == n, name
    assert report.ok
    bs = report.tables["BLOCKSCTN"]
    assert (bs.blank_rows, bs.header_rows, bs.duplicate_rows) == (5, 1, 62)
    assert report.tables["FREIGHT"].duplicate_rows == 5


def test_no_date_parse_errors_and_99_means_2099(
    stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path],
) -> None:
    tables, report, _ = stage1
    for t in report.tables.values():
        assert sum(t.date_parse_errors.values()) == 0, t.name
    assert set(tables["STATION"]["MADVALIDTO"]) == {dt.date(2099, 12, 31)}
    assert dt.date(2099, 12, 31) in set(tables["TRAIN"]["VALIDTO"])
    assert all(d.year != 1999 for d in tables["SCHEDULE"]["VALIDTO"].dropna())


def test_spreadsheet_columns_dropped_and_empty(
    stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path],
) -> None:
    tables, report, _ = stage1
    assert not any(c.startswith("Unnamed") for df in tables.values() for c in df.columns)
    assert all(t.unnamed_nonempty_cells == 0 for t in report.tables.values())


def test_schedule_seconds_split(stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path]) -> None:
    sched = stage1[0]["SCHEDULE"]
    arr = sched["ARRIVAL"]
    assert (sched["ARRIVAL__day_offset"] * SECONDS_PER_DAY + sched["ARRIVAL__time_s"] == arr).all()
    # DAY_OF_RUN follows the DEPARTURE time: floor(DEPARTURE / 86400) + 1 holds for all 4,260 rows.
    # R06 §3 / spec DQ-19 state it with ARRIVAL, which differs for one stop across midnight
    # (arrive 23:55 day 1, depart 00:20 day 2).
    day_of_run = sched["DAY_OF_RUN"].astype(int)
    assert (sched["DEPARTURE__day_offset"] + 1 == day_of_run).all()
    assert int((sched["ARRIVAL__day_offset"] + 1 != day_of_run).sum()) == 1


def test_freight_timestamps_and_checks(stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path]) -> None:
    tables, report, _ = stage1
    freight = tables["FREIGHT"]
    assert report.tables["FREIGHT"].date_parse_errors == {"Arrival Time": 0, "Depart Time": 0}
    assert freight["Arrival Time"].dt.tz is not None
    assert report.freight is not None
    assert report.freight.loads_out_of_order == 0
    # Division-wide log: 2 negative dwells (SPRD on the Rayagada branch, PDU in the section).
    assert report.freight.negative_dwell_rows == 2
    stations = set(tables["ROUTE_DN"]["STATION_CODE"])
    in_section = freight[NEGATIVE_DWELL] & freight["Block Section"].map(
        lambda b: all(s in stations for s in str(b).split("-"))
    )
    assert int(in_section.sum()) == 1  # matches R06 §4.11 for KTV-PSA


def test_outputs_written_and_raw_untouched(
    stage1: tuple[dict[str, pd.DataFrame], Stage1Report, Path],
) -> None:
    _, _, out = stage1
    assert (out / "stage1_report.json").exists()
    assert (out / "schedule.parquet").exists()
    assert manifest.diff(REPO / "data" / "raw" / "ktv-psa") == []


def test_refuses_to_write_inside_raw() -> None:
    with pytest.raises(ValueError, match="C-13"):
        run_stage1(out_dir=REPO / "data" / "raw" / "ktv-psa" / "out")
