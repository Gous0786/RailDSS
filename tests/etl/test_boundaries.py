"""Boundary and junction flow tests (issue #12 acceptance criteria)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from raildss_etl.boundaries import LOCAL, BoundaryConfig, BoundaryReport, Flow, build_boundaries, load_config
from raildss_etl.freight import build_freight
from raildss_etl.load import run_stage1
from raildss_etl.timetable import build_timetable, ist
from raildss_etl.topology import build_topology

Built = tuple[dict[str, Flow], dict[str, Flow], BoundaryReport, dict[str, pd.DataFrame]]


@pytest.fixture(scope="module")
def built() -> Built:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    topo = build_topology(tables)[0]
    demand = build_freight(tables, topo)[0]
    wtt, freight, report = build_boundaries(build_timetable(tables)[0], demand, tables, topo, ist())
    return wtt, freight, report, tables


def _cfg(**extra: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "boundaries": {
            "A-X": {"station": "A", "blocks": ["A-X"], "default": True},
            "A-Y": {"station": "A", "blocks": ["A-Y"]},
        }
    }
    base.update(extra)
    return base


def test_every_train_and_load_has_entry_and_exit(built: Built) -> None:
    wtt, freight, report, _ = built
    assert report.wtt_trains == 250 and report.freight_loads == 2604
    assert report.wtt_unassigned == [] and report.freight_unassigned == []
    boundaries = set(load_config().boundaries)
    for flow in [*wtt.values(), *freight.values()]:
        for end in (flow.entry, flow.exit):
            assert end in boundaries or end.startswith(LOCAL), flow
    assert report.ok


def test_daily_counts_match_freight_log(built: Built) -> None:
    _, freight, report, tables = built
    assert report.freight_day_totals_match
    assert len(report.entries_per_day) == 27
    # A load whose log origin / destination division is elsewhere never enters / exits as local.
    assert report.freight_local_but_outside_od == {"entry": 0, "exit": 0}
    # Loads whose division log starts at PUN crossed from the Khurda Road side.
    first = tables["FREIGHT"].sort_values(["LoadId", "Arrival Time"]).groupby("LoadId").first()
    from_pun = [lid for lid in freight if first.loc[lid, "Sttn"] == "PUN"]
    assert from_pun and all(freight[lid].entry == "PSA-KUR" for lid in from_pun)
    day = next(iter(report.entries_per_day.values()))
    assert set(day) == {"wtt", "freight"} and sum(day["freight"].values()) > 0


def test_wtt_rules(built: Built) -> None:
    wtt, _, report, tables = built
    trains = tables["TRAIN"].set_index("TRAINID")
    assert report.wtt_local_exit_destination_mismatch == []
    for tid, flow in wtt.items():
        if str(trains.loc[tid, "SOURCE"]) == "KRDL":  # Kirandul trains use the KK line at KTV
            assert flow.entry == "KTV-KK" and flow.entry_rule == "reverse train"
        if str(trains.loc[tid, "DESTINATION"]) == "KRDL":
            assert flow.exit == "KTV-KK"
        if flow.entry_rule == "originates":
            assert flow.entry == f"{LOCAL}{trains.loc[tid, 'SOURCE']}"


def test_acceptance_windows_default_open_and_validated() -> None:
    cfg = BoundaryConfig.model_validate(_cfg())
    assert cfg.is_open("A-X", dt.time(3, 0))
    cfg = BoundaryConfig.model_validate(
        _cfg(acceptance_windows={"A-X": [{"start": "06:00", "end": "08:00"}]})
    )
    assert cfg.is_open("A-X", dt.time(7, 59)) and not cfg.is_open("A-X", dt.time(8, 0))
    assert cfg.is_open("A-Y", dt.time(8, 0))
    assert load_config().acceptance_windows == {}


@pytest.mark.parametrize(
    "extra",
    [
        {
            "acceptance_windows": {
                "A-X": [{"start": "06:00", "end": "08:00"}, {"start": "07:30", "end": "09:00"}]
            }
        },
        {"acceptance_windows": {"A-X": [{"start": "08:00", "end": "06:00"}]}},
        {"acceptance_windows": {"A-Z": []}},
        {"gap_fill": {"entry": {"DN": "A-Z"}}},
    ],
)
def test_invalid_configuration_rejected(extra: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BoundaryConfig.model_validate(_cfg(**extra))


def test_one_default_per_station_and_unique_blocks() -> None:
    bad = _cfg()
    bad["boundaries"]["A-Y"]["default"] = True
    with pytest.raises(ValidationError):
        BoundaryConfig.model_validate(bad)
    bad = _cfg()
    bad["boundaries"]["A-Y"]["blocks"] = ["A-X"]
    with pytest.raises(ValidationError):
        BoundaryConfig.model_validate(bad)
