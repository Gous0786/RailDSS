"""Scenario format and library v0 (spec FR-SCN-01, FR-SCN-04; issue #21)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from raildss_etl.publish import PUBLISHED_DIR, read_export
from raildss_twin import run, scenario
from raildss_twin.scenario import Scenario

LIBRARY = scenario.load_library()
EXECUTABLE = [s for s in LIBRARY if s.executable]
REAL_DAYS = ("SCN-01", "SCN-22")


def _base() -> dict[str, Any]:
    return yaml.safe_load((scenario.LIBRARY_DIR / "SCN-01.yaml").read_text(encoding="utf-8"))


def test_the_ten_v0_files_validate_against_the_schema() -> None:
    assert [s.id for s in LIBRARY] == [
        "SCN-01",
        "SCN-02",
        "SCN-03",
        "SCN-05",
        "SCN-06",
        "SCN-12",
        "SCN-13",
        "SCN-16",
        "SCN-20",
        "SCN-22",
    ]
    assert all(s.schema_version == scenario.SCHEMA_VERSION for s in LIBRARY)


def test_the_files_name_the_published_network_and_resolve_their_locations() -> None:
    meta, tables, _ = read_export(PUBLISHED_DIR / scenario.NETWORK_VERSION)
    for s in LIBRARY:
        assert scenario.check_references(s, meta, tables) == [], s.id


def test_the_index_lists_the_scenarios_by_family_and_is_current() -> None:
    text = scenario.index_markdown(LIBRARY)
    assert scenario.INDEX_FILE.read_text(encoding="utf-8") == text
    for s in LIBRARY:
        assert f"| {s.id} |" in text and f"## {s.family}" in text
    assert len({s.family for s in LIBRARY}) >= 8


def test_the_v0_mix_meets_fr_scn_04() -> None:
    single_line = [s for s in LIBRARY if s.family == "Single-line working"]
    real_days = [s for s in LIBRARY if s.freight_day is not None and s.executable]
    assert {s.id for s in single_line} >= {"SCN-05", "SCN-20"}
    assert len(real_days) >= 2


def test_disruption_scenarios_are_tagged_requires_injector_and_are_not_executed() -> None:
    disrupted = [s for s in LIBRARY if s.disruptions]
    assert {s.id for s in disrupted} == {"SCN-02", "SCN-03", "SCN-05", "SCN-06", "SCN-20"}
    for s in disrupted:
        assert s.requires == ["injector"] and not s.executable and s.expected_gt_hash is None
        with pytest.raises(ValueError, match="requires injector"):
            scenario.run_scenario(None, s)


def test_scripted_disruptions_realize_into_injector_disruptions_from_the_seed() -> None:
    for s in LIBRARY:
        for d in s.disruptions:
            a = d.realize(s.seed, s.wtt_date)
            b = d.realize(s.seed, s.wtt_date)
            if d.type == "entry_delay":
                assert a is None
                continue
            assert a is not None and a == b
            assert d.duration_min.low_min * 60 <= a.duration_s <= d.duration_min.high_min * 60
            assert a.start_s == d.start_s
            assert (a.etr_s is None) == (d.etr is None)
            assert a != d.realize(s.seed + 1, s.wtt_date) or d.duration_min.low_min == d.duration_min.high_min


def test_the_schema_rejects_inconsistent_scenarios() -> None:
    base = _base()
    cases = {
        "no day": dict(base, freight_day=None),
        "two days": dict(base, nhpp_seed=3),
        "day does not match the date": dict(base, wtt_date=dt.date(2025, 8, 2)),
        "executable without a hash": dict(base, expected_gt_hash=None),
        "bad hash": dict(base, expected_gt_hash="abc"),
        "unknown field": dict(base, surprise=1),
        "disruption without the tag": dict(
            base,
            disruptions=[
                {
                    "incident_id": "X",
                    "type": "line_block",
                    "block_id": "CPP-SGDM",
                    "start": "10:00",
                    "duration_min": {"low_min": 10, "high_min": 20},
                }
            ],
        ),
        "tag with a hash": dict(base, requires=["injector"]),
    }
    for name, data in cases.items():
        with pytest.raises(ValidationError):
            Scenario.model_validate(data)
        assert name


def test_a_disruption_needs_its_location_and_a_scenario_file_is_named_by_its_id(tmp_path: Any) -> None:
    with pytest.raises(ValidationError, match="needs"):
        scenario.ScenarioDisruption.model_validate(
            {
                "incident_id": "X",
                "type": "tsr",
                "block_id": "CPP-SGDM",
                "start": "10:00",
                "duration_min": {"low_min": 1, "high_min": 2},
            }
        )
    path = tmp_path / "SCN-99.yaml"
    path.write_text((scenario.LIBRARY_DIR / "SCN-01.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="file name"):
        scenario.load(path)


def test_record_hash_changes_only_the_hash_line(tmp_path: Any) -> None:
    path = tmp_path / "SCN-01.yaml"
    original = (scenario.LIBRARY_DIR / "SCN-01.yaml").read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    scenario.record_hash(path, "f" * 64)
    changed = path.read_text(encoding="utf-8").splitlines()
    same = original.splitlines()
    assert [a for a, b in zip(same, changed, strict=True) if a != b] == [
        next(x for x in same if x.startswith("expected_gt_hash"))
    ]
    assert scenario.load(path).expected_gt_hash == "f" * 64


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.mark.parametrize("sc", EXECUTABLE, ids=lambda s: s.id)
def test_executable_scenarios_run_in_the_twin_with_their_recorded_gt_hash(
    world: run.World, sc: Scenario
) -> None:
    report = scenario.run_scenario(world, sc)
    assert report.completed and not report.stuck and not report.deadlocks, sc.id
    assert report.seed == sc.seed
    assert report.trace_hash == sc.expected_gt_hash, f"{sc.id}: re-record with `scenario run --record`"


def test_the_two_real_day_scenarios_are_executable_and_run_in_ci() -> None:
    assert {s.id for s in EXECUTABLE} >= set(REAL_DAYS)
