"""WTT import and dated expansion tests (issue #10 acceptance criteria)."""

from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from raildss_etl.load import run_stage1
from raildss_etl.timetable import (
    Stage5Report,
    Timetable,
    build_timetable,
    ist,
    trains_on_stretches,
    trains_per_block,
)
from raildss_etl.topology import build_topology

DAY = dt.date(2025, 8, 20)  # a Wednesday


@pytest.fixture(scope="module")
def data() -> tuple[Timetable, Stage5Report, dict[str, str]]:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    tt, rep = build_timetable(tables)
    topo, _ = build_topology(tables)
    km = {o.code: o.km for o in topo.operational_points}
    stretch = {
        b.id: ("ktv_vzm" if max(km[b.from_op], km[b.to_op]) <= km["VZM"] else "vzm_psa")
        for b in topo.block_sections
    }
    return tt, rep, stretch


def test_dq19_and_counts(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    _, rep, _ = data
    assert rep.ok
    assert (rep.services, rep.timetable_points, rep.orphan_points) == (250, 4260, 0)
    assert rep.non_monotonic == []
    assert (rep.nextarvl_mismatches, rep.day_of_run_mismatches, rep.busy_days_mismatches) == (0, 0, 0)


def test_stops_and_dwell(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    rep = data[1]
    assert (rep.stops, rep.median_dwell_s) == (722, 120.0)


def test_every_service_has_a_priority_class(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    tt, rep, _ = data
    assert rep.unmapped_train_types == []
    assert all(s.priority_class != "unmapped" for s in tt.services)


def test_monday_first_weekdays(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    tt = data[0]
    garib_rath = next(s for s in tt.services if s.train_no == "22883")  # departs Puri on Fridays
    assert [i for i, runs in enumerate(garib_rath.days) if runs] == [dt.date(2025, 8, 22).weekday()]
    vande_bharat = next(s for s in tt.services if s.train_no == "20829")  # all days except Thursday
    assert not vande_bharat.days[dt.date(2025, 8, 21).weekday()]
    assert sum(vande_bharat.days) == 6


def test_trains_per_day_match_r06(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    tt, _, stretch = data
    per_block = trains_per_block(tt, DAY, stretch)
    kv = [n for (s, _, _), n in per_block.items() if s == "ktv_vzm"]
    vp = [n for (s, _, _), n in per_block.items() if s == "vzm_psa"]
    assert min(kv) >= 35 and max(kv) <= 41  # R06 §2: KTV-VZM 35-41 per direction
    assert (
        min(vp) >= 25 and max(vp) <= 31
    )  # R06 §2: VZM-PSA "~26-30"; measured 25-31 (approximate figure, +/-1)
    stretches = trains_on_stretches(tt, DAY, stretch)
    assert stretches[("ktv_vzm", "DN")] == 41 and stretches[("vzm_psa", "DN")] == 33  # NWP joins/leaves


def test_dated_runs(data: tuple[Timetable, Stage5Report, dict[str, str]]) -> None:
    runs = data[0].runs_on(DAY, ist(), "KTV-PSA-2025-08")
    assert runs and all(r.run_id.endswith(r.run_date.isoformat()) for r in runs)
    assert all(r.entry_time is not None and r.entry_time.utcoffset() == dt.timedelta(0) for r in runs)
    friday_only = {r.train_id for r in runs if r.run_id.startswith("22883@")}
    assert not friday_only  # 22883 does not start on a Wednesday
