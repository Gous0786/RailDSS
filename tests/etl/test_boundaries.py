"""Boundary and junction flow tests (issue #12 acceptance criteria)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pandas as pd
import pytest
from pydantic import ValidationError

from raildss_etl.boundaries import (
    LOCAL,
    BoundaryConfig,
    BoundaryReport,
    Flow,
    Window,
    build_boundaries,
    freight_day_mismatches,
    load_config,
    local_division_violations,
)
from raildss_etl.freight import FreightDemand, build_freight
from raildss_etl.load import run_stage1
from raildss_etl.timetable import build_timetable, ist
from raildss_etl.topology import build_topology

Built = tuple[dict[str, Flow], dict[str, Flow], BoundaryReport, dict[str, pd.DataFrame]]


@pytest.fixture(scope="module")
def inputs() -> tuple[dict[str, pd.DataFrame], FreightDemand, set[str]]:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    topo = build_topology(tables)[0]
    return tables, build_freight(tables, topo)[0], {o.code for o in topo.operational_points}


@pytest.fixture(scope="module")
def built(inputs: tuple[dict[str, pd.DataFrame], FreightDemand, set[str]]) -> Built:
    tables, demand, _ = inputs
    topo = build_topology(tables)[0]
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
    assert report.freight_day_mismatches == []
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


def test_day_check_fails_when_a_load_is_missing(
    inputs: tuple[dict[str, pd.DataFrame], FreightDemand, set[str]],
) -> None:
    tables, demand, section = inputs
    assert freight_day_mismatches(demand, tables["FREIGHT"], section, ist()) == []
    days = [d.model_copy(deep=True) for d in demand.days]
    dropped = days[5].run_ids.pop()
    broken = demand.model_copy(update={"days": days})
    problems = freight_day_mismatches(broken, tables["FREIGHT"], section, ist())
    assert len(problems) == 1 and "entry" in problems[0] and dropped in problems[0]
    # An exit moved to another day is caught as well.
    loads = list(demand.loads)
    assert loads[0].exit_time is not None
    loads[0] = loads[0].model_copy(update={"exit_time": loads[0].exit_time + dt.timedelta(days=2)})
    problems = freight_day_mismatches(
        demand.model_copy(update={"loads": loads}), tables["FREIGHT"], section, ist()
    )
    assert sum("exit" in p for p in problems) == 2  # missing on one day, extra on another


def test_division_check_fails_on_inconsistent_log(built: Built) -> None:
    _, freight, _, tables = built
    log = tables["FREIGHT"]
    assert local_division_violations(freight, log) == {"entry": 0, "exit": 0}
    local_in = next(lid for lid, f in freight.items() if f.entry.startswith(LOCAL))
    local_out = next(lid for lid, f in freight.items() if f.exit.startswith(LOCAL))
    bad = log.copy()
    bad.loc[bad["LoadId"] == local_in, "From Division"] = "KUR"
    bad.loc[bad["LoadId"] == local_out, "Division To"] = "KUR"
    assert local_division_violations(freight, bad) == {"entry": 1, "exit": 1}


# ------------------------------------------------------ weekday and date-range windows (DR-0002 F11)

AUGUST_SUNDAYS = {"weekdays": ["Sun"], "valid_from": "2025-08-01", "valid_to": "2025-08-31"}
CLOSURE = {
    "A-X": [
        {"start": "00:00", "end": "02:00", **AUGUST_SUNDAYS},
        {"start": "04:00", "end": "24:00", **AUGUST_SUNDAYS},
    ]
}


def test_a_sunday_closure_in_august_can_be_expressed_and_validates() -> None:
    """Closed 02:00-04:00 on Sundays in August; open at every other time."""
    cfg = BoundaryConfig.model_validate(_cfg(acceptance_windows=CLOSURE))
    sunday, monday = dt.date(2025, 8, 3), dt.date(2025, 8, 4)
    assert not cfg.is_open("A-X", dt.datetime.combine(sunday, dt.time(3, 0)))
    assert not cfg.is_open("A-X", dt.time(2, 0), day=sunday)
    assert cfg.is_open("A-X", dt.time(1, 59), day=sunday) and cfg.is_open("A-X", dt.time(4, 0), day=sunday)
    assert cfg.is_open("A-X", dt.datetime.combine(monday, dt.time(3, 0)))  # not a Sunday
    assert cfg.is_open(
        "A-X", dt.datetime.combine(dt.date(2025, 9, 7), dt.time(3, 0))
    )  # a Sunday in September
    assert cfg.is_open("A-X", dt.datetime.combine(dt.date(2025, 7, 27), dt.time(3, 0)))  # a Sunday in July


def test_a_time_without_a_date_only_meets_unrestricted_windows() -> None:
    cfg = BoundaryConfig.model_validate(_cfg(acceptance_windows=CLOSURE))
    assert cfg.is_open("A-X", dt.time(3, 0))
    plain = BoundaryConfig.model_validate(
        _cfg(acceptance_windows={"A-X": [{"start": "06:00", "end": "08:00"}]})
    )
    assert not plain.is_open("A-X", dt.time(9, 0))


def test_valid_dates_are_inclusive_and_weekdays_are_monday_first() -> None:
    w = Window(
        start="00:00",
        end="24:00",
        weekdays=["Mon"],
        valid_from=dt.date(2025, 8, 4),
        valid_to=dt.date(2025, 8, 11),
    )
    assert w.applies_on(dt.date(2025, 8, 4)) and w.applies_on(dt.date(2025, 8, 11))  # both Mondays, both ends
    assert not w.applies_on(dt.date(2025, 8, 18)) and not w.applies_on(dt.date(2025, 8, 5))
    assert Window(start="00:00", end="24:00").applies_on(dt.date(2030, 1, 1))  # no limits: every day


@pytest.mark.parametrize(
    ("a", "b", "overlap"),
    [
        ({"start": "06:00", "end": "08:00"}, {"start": "07:30", "end": "09:00"}, True),
        ({"start": "06:00", "end": "08:00"}, {"start": "08:00", "end": "09:00"}, False),  # adjacent
        (
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun"]},
            {"start": "07:00", "end": "09:00", "weekdays": ["Mon"]},
            False,
        ),
        (
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun", "Mon"]},
            {"start": "07:00", "end": "09:00", "weekdays": ["Mon"]},
            True,
        ),
        ({"start": "06:00", "end": "08:00", "weekdays": ["Sun"]}, {"start": "07:00", "end": "09:00"}, True),
        (
            {"start": "06:00", "end": "08:00", "valid_to": "2025-07-31"},
            {"start": "07:00", "end": "09:00", "valid_from": "2025-08-01"},
            False,
        ),
        (
            {"start": "06:00", "end": "08:00", "valid_to": "2025-08-01"},
            {"start": "07:00", "end": "09:00", "valid_from": "2025-08-01"},
            True,
        ),
        # date ranges meet, weekdays differ on every shared day
        (
            {
                "start": "06:00",
                "end": "08:00",
                "weekdays": ["Mon"],
                "valid_from": "2025-08-04",
                "valid_to": "2025-08-09",
            },
            {
                "start": "06:00",
                "end": "08:00",
                "weekdays": ["Tue"],
                "valid_from": "2025-08-04",
                "valid_to": "2025-08-09",
            },
            False,
        ),
        # the shared weekday (Sun) falls outside the shared dates (4-5 Aug)
        (
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun"], "valid_to": "2025-08-05"},
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun", "Tue"], "valid_from": "2025-08-04"},
            False,
        ),
        # a shared Sunday inside the shared dates
        (
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun"], "valid_to": "2025-08-05"},
            {"start": "06:00", "end": "08:00", "weekdays": ["Sun", "Tue"], "valid_from": "2025-08-01"},
            True,
        ),
    ],
)
def test_overlap_considers_minutes_weekdays_and_dates(
    a: dict[str, Any], b: dict[str, Any], overlap: bool
) -> None:
    wa, wb = Window.model_validate(a), Window.model_validate(b)
    assert wa.overlaps(wb) is overlap and wb.overlaps(wa) is overlap
    cfg = _cfg(acceptance_windows={"A-X": [a, b]})
    if overlap:
        with pytest.raises(ValidationError, match="overlap"):
            BoundaryConfig.model_validate(cfg)
    else:
        BoundaryConfig.model_validate(cfg)


@pytest.mark.parametrize(
    "window",
    [
        {"start": "00:00", "end": "24:00", "weekdays": ["Funday"]},
        {"start": "00:00", "end": "24:00", "weekdays": ["Sun", "Sun"]},
        {"start": "00:00", "end": "24:00", "valid_from": "2025-08-31", "valid_to": "2025-08-01"},
        {"start": "00:00", "end": "24:00", "valid_from": "not a date"},
    ],
)
def test_bad_weekdays_or_dates_are_rejected(window: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BoundaryConfig.model_validate(_cfg(acceptance_windows={"A-X": [window]}))


def test_the_committed_config_still_loads_with_no_windows() -> None:
    assert load_config().acceptance_windows == {}
