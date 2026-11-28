"""SCN-22 over the 27 real freight days (issue #166): every day replays with its recorded hash, and the BL-5
comparison is committed and current with its rows."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from raildss_twin import real_days, run, scenario
from raildss_twin.bl0_check import REFERENCE_JSON

SCN22 = next(s for s in scenario.load_library_v1() if s.id == "SCN-22")
DAYS = scenario.day_scenarios(SCN22)


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def test_scn22_lists_the_27_real_days_each_with_a_recorded_hash() -> None:
    assert [d.wtt_date for d in DAYS] == [dt.date(2025, 8, k) for k in range(1, 28)]
    assert all(d.freight_day == f"aug25-day-{d.wtt_date.day:02d}" for d in DAYS)
    assert all(d.seed == SCN22.seed and d.expected_gt_hash for d in DAYS)
    day2 = next(d for d in DAYS if d.wtt_date.day == 2)
    assert day2.expected_gt_hash == SCN22.expected_gt_hash  # the scenario's own day is one of them


@pytest.mark.parametrize("sc", DAYS, ids=lambda s: s.wtt_date.isoformat())
def test_every_real_day_replays_with_its_recorded_hash(world: run.World, sc: scenario.Scenario) -> None:
    report = scenario.run_scenario(world, sc)
    assert report.completed and not report.stuck and not report.deadlocks, sc.wtt_date
    assert report.trace_hash == sc.expected_gt_hash, (
        f"{sc.wtt_date}: re-record with `scenario run SCN-22 --record --days {sc.wtt_date.day}`"
    )


def test_a_real_day_hash_is_recorded_in_its_own_entry(tmp_path: Path) -> None:
    path = tmp_path / "SCN-22.yaml"
    path.write_text(scenario.path_of(SCN22).read_text(encoding="utf-8"), encoding="utf-8")
    scenario.record_day_hash(path, dt.date(2025, 8, 5), "f" * 64)
    again = scenario.load(path)
    assert next(d for d in again.real_days if d.wtt_date.day == 5).expected_gt_hash == "f" * 64
    others = [d for d in again.real_days if d.wtt_date.day != 5]
    assert others == [d for d in SCN22.real_days if d.wtt_date.day != 5]
    with pytest.raises(ValueError):
        scenario.record_day_hash(path, dt.date(2025, 9, 1), "f" * 64)


def test_a_real_day_listed_twice_is_rejected() -> None:
    doc: dict[str, Any] = SCN22.model_dump(mode="json")
    doc["real_days"] = [*doc["real_days"], doc["real_days"][0]]
    with pytest.raises(ValueError):
        scenario.Scenario.model_validate(doc)


def test_the_committed_bl5_comparison_has_every_day_and_is_rendered_from_its_rows() -> None:
    rows = real_days.load_rows()
    assert sorted(rows) == [d.wtt_date.isoformat() for d in DAYS]
    reference = json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))
    text = real_days.render(rows, reference)
    assert real_days.REPORT_MD.read_text(encoding="utf-8") == text
    assert "Days run: 27 of 27." in text and "| 2025-08-20 |" in text
    for r in rows.values():
        assert r["twin_transit_n"] > 0 and r["twin_vzm_calls"] > 0 and r["hist_transit_median_h"] is not None
