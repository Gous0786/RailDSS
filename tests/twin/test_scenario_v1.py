"""Scenario library v1: definitions, index and the executable subset (issue #165, spec FR-SCN-04)."""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from raildss_etl.publish import PUBLISHED_DIR, read_export
from raildss_twin import run, scenario
from raildss_twin.scenario import Scenario, ScenarioDisruption

V0 = scenario.load_library()
LIBRARY = scenario.load_library_v1()
NEW = [s for s in LIBRARY if s.id not in {x.id for x in V0}]
EXECUTABLE_NEW = [s for s in NEW if s.executable]
WAITING = [s for s in LIBRARY if not s.executable]
NEW_IDS = ["SCN-04", "SCN-07", "SCN-08", "SCN-09", "SCN-10", "SCN-11", "SCN-14", "SCN-15", "SCN-19"]
NEW_IDS += ["SCN-21", "SCN-23", "SCN-24"]


def _new() -> dict[str, Any]:
    return dict(yaml.safe_load((scenario.V1_DIR / "SCN-07.yaml").read_text(encoding="utf-8")))


def test_library_v1_is_the_v0_ten_plus_the_twelve_new_definitions() -> None:
    assert len(LIBRARY) >= 22
    assert [s.id for s in NEW] == NEW_IDS
    assert {s.id for s in V0} <= {s.id for s in LIBRARY}
    assert scenario.check_library(LIBRARY) == []


def test_the_files_name_the_published_network_and_resolve_their_locations() -> None:
    meta, tables, _ = read_export(PUBLISHED_DIR / scenario.NETWORK_VERSION)
    for s in LIBRARY:
        assert scenario.check_references(s, meta, tables) == [], s.id


def test_the_v1_index_lists_every_scenario_by_family_and_is_current() -> None:
    text = scenario.index_markdown(LIBRARY, v1=True)
    assert scenario.V1_INDEX_FILE.read_text(encoding="utf-8") == text
    for s in LIBRARY:
        assert f"| {s.id} |" in text and f"## {s.family}" in text
    assert len({s.family for s in LIBRARY}) >= 8


def test_every_v1_scenario_is_executable_and_carries_its_recorded_hash() -> None:
    """Issue #167: no scenario waits for a component any more; the CI tests below run every one of them."""
    assert [s.id for s in WAITING] == [] and len(LIBRARY) == 22
    assert {s.id for s in EXECUTABLE_NEW} == set(NEW_IDS)
    assert all(s.requires == [] and s.expected_gt_hash for s in LIBRARY)
    hashes = [s.expected_gt_hash for s in LIBRARY]
    assert len(set(hashes)) == len(hashes)  # no two scenarios share a ground truth


def test_the_new_scenarios_cover_the_spec_table_and_name_a_real_day() -> None:
    assert set(NEW_IDS) | {s.id for s in V0} >= {f"SCN-{n:02d}" for n in (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)}
    assert {"SCN-14", "SCN-15", "SCN-19", "SCN-20", "SCN-21", "SCN-23", "SCN-24"} <= {s.id for s in LIBRARY}
    assert all(s.freight_day is not None for s in NEW)  # every new one replays a real freight day
    assert next(s for s in NEW if s.id == "SCN-24").base == ["SCN-01", "SCN-16"]


def test_the_new_disruption_kinds_need_their_fields() -> None:
    common = {"incident_id": "X", "start": "10:00", "duration_min": {"low_min": 1, "high_min": 2}}
    ok = {
        "ohe_block": {"block_id": "GVI-CPP"},
        "ohe_trip": {"block_id": "GVI-CPP"},
        "crew_hours": {"train_no": "T", "station": "VZM"},
        "exit_blocked": {"boundary": "KTV-VSKP"},
    }
    for kind, fields in ok.items():
        ScenarioDisruption.model_validate({**common, "type": kind, **fields})
        with pytest.raises(ValidationError, match="needs"):
            ScenarioDisruption.model_validate({**common, "type": kind})
    special = {
        "incident_id": "X",
        "type": "special_train",
        "start": "13:00",
        "train_no": "S",
        "station": "KTV",
    }
    with pytest.raises(ValidationError, match="category, direction"):
        ScenarioDisruption.model_validate(special)
    d = ScenarioDisruption.model_validate({**special, "category": "mail_express", "direction": "DN"})
    assert d.duration_min is None  # an insertion has no duration
    assert (
        d.realize(1, scenario.load(scenario.V1_DIR / "SCN-11.yaml").wtt_date) is None
    )  # played by the scenario


def test_check_library_catches_unknown_bases_duplicates_and_unbounded_sensitivity() -> None:
    base = Scenario.model_validate(_new())
    assert scenario.check_library([base]) == []
    assert any(
        "not in the library" in p
        for p in scenario.check_library([base.model_copy(update={"base": ["SCN-99"]})])
    )
    assert any("defined twice" in p for p in scenario.check_library([base, base]))
    wrong = base.model_copy(update={"sensitivity": {"twin.injector": "low"}})
    assert any("no bounds" in p for p in scenario.check_library([wrong]))
    assert any(
        "no bounds" in p
        for p in scenario.check_library([base.model_copy(update={"sensitivity": {"x.y": "low"}})])
    )


def test_an_unknown_boundary_is_reported() -> None:
    meta, tables, _ = read_export(PUBLISHED_DIR / scenario.NETWORK_VERSION)
    sc = scenario.load(scenario.V1_DIR / "SCN-23.yaml")
    assert scenario.check_references(sc, meta, tables) == []
    bad = sc.model_copy(update={"disruptions": [sc.disruptions[0].model_copy(update={"boundary": "NOPE"})]})
    assert any("boundary NOPE" in p for p in scenario.check_references(bad, meta, tables))


def test_a_scenario_waiting_for_a_component_is_validated_not_executed() -> None:
    sc = Scenario.model_validate(dict(_new(), requires=["injector-loco"], expected_gt_hash=None))
    assert not sc.executable
    with pytest.raises(ValueError, match="requires injector-loco"):
        scenario.run_scenario(None, sc)


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.mark.parametrize("sc", EXECUTABLE_NEW, ids=lambda s: s.id)
def test_executable_v1_scenarios_run_in_the_twin_with_their_recorded_gt_hash(
    world: run.World, sc: Scenario
) -> None:
    report = scenario.run_scenario(world, sc)
    assert report.completed and not report.stuck and not report.deadlocks, sc.id
    assert report.trace_hash == sc.expected_gt_hash, f"{sc.id}: re-record with `scenario run --record`"


@pytest.mark.parametrize("sc", [s for s in EXECUTABLE_NEW if s.disruptions], ids=lambda s: s.id)
def test_a_v1_disruption_scenario_changes_the_ground_truth(world: run.World, sc: Scenario) -> None:
    plain, _ = run.run_day(world, sc.wtt_date, seed=sc.seed, day_instance=sc.freight_day)
    assert plain.trace_hash != sc.expected_gt_hash


def test_a_sensitivity_that_the_scenarios_cannot_apply_is_reported() -> None:
    base = Scenario.model_validate(_new())
    unknown = base.model_copy(update={"sensitivity": {"station_line.csr.stats": "low"}})  # has no bounds
    assert any("can apply" in p for p in scenario.check_library([unknown]))
    assert scenario.check_library(LIBRARY) == []


def test_the_sensitivity_world_sizes_only_the_psa_lines_and_leaves_the_world_alone(world: run.World) -> None:
    sc = next(s for s in LIBRARY if s.id == "SCN-24")
    low_lines: list[Any] = list(scenario.sensitivity_world(world, sc).tables["station_line"])
    lines: list[Any] = list(world.tables["station_line"])
    bounds = world.register.get("station_line.csr.psa").bounds
    assert bounds is not None
    for x in low_lines:
        if x.op_code == "PSA" and x.kind in bounds.low:
            assert x.csr_m == bounds.low[x.kind]  # loops 673 m, mains 678 m
    assert {x.csr_m for x in lines if x.op_code == "PSA"} == {
        733.0,
        752.0,
    }  # the published world is untouched
    assert [x for x in low_lines if x.op_code != "PSA"] == [x for x in lines if x.op_code != "PSA"]
    assert scenario.sensitivity_world(world, next(s for s in LIBRARY if s.id == "SCN-01")) is world


def test_the_low_bound_changes_the_ground_truth_of_scn_24_and_of_its_base_scenarios(world: run.World) -> None:
    sc24 = next(s for s in LIBRARY if s.id == "SCN-24")
    central, _ = run.run_day(world, sc24.wtt_date, seed=sc24.seed, day_instance=sc24.freight_day)
    assert scenario.run_scenario(world, sc24).trace_hash != central.trace_hash
    for base_id in sc24.base:
        base = next(s for s in LIBRARY if s.id == base_id)
        with_low = scenario.run_scenario(world, base.model_copy(update={"sensitivity": sc24.sensitivity}))
        assert with_low.completed and not with_low.stuck and not with_low.deadlocks, base_id
