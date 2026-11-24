"""Disruption and what-if export of a scenario for the web UI (issue #172, FR-UI-11)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from raildss_orchestrator import whatif
from raildss_twin import run, scenario

LIBRARY = {s.id: s for s in scenario.load_library_v1()}
MIN = 60.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def test_a_line_block_becomes_an_incident_with_its_location_etr_and_real_end(world: run.World) -> None:
    sc = LIBRARY["SCN-05"]
    (rec,) = whatif.disruption_records(world, sc)
    assert rec["id"] == "SCN05-DN-CPP-SGDM" and rec["type"] == "line_block" and rec["line"] == "DN"
    assert rec["location"] == "CPP-SGDM" and rec["lines_affected"] == ["CPP-SGDM/DN"]
    assert rec["single_line_working"] is True and rec["start_s"] == 10 * 3600
    assert 120 * MIN <= rec["end_s"] - rec["start_s"] <= 180 * MIN  # the seeded real duration
    assert rec["etr_s"] == rec["end_s"] + 30 * MIN and rec["etr_sigma_min"] == 20.0  # the announced ETR
    assert rec["etr_source"] == "SM_ESTIMATE"
    assert 0 < rec["km_from"] < rec["km_to"]
    assert whatif.disruption_records(world, sc) == [rec]  # deterministic


@pytest.mark.parametrize("sid", ["SCN-02", "SCN-11", "SCN-23", "SCN-15", "SCN-24"])
def test_every_kind_of_scenario_disruption_is_listed(world: run.World, sid: str) -> None:
    sc = LIBRARY[sid]
    recs = whatif.disruption_records(world, sc)
    assert {r["id"] for r in recs} == {d.incident_id for d in sc.disruptions}
    assert all(r["end_s"] >= r["start_s"] and r["type"] and r["owner"] for r in recs)
    if sid == "SCN-02":
        assert (
            recs[0]["etr_s"] is None and recs[0]["end_s"] > recs[0]["start_s"]
        )  # a late entry: no ETR announced
    if sid == "SCN-11":
        assert recs[0]["linked_trains"] == ["SPL-11@2025-08-18"] and recs[0]["end_s"] == recs[0]["start_s"]
    if sid == "SCN-23":
        assert recs[0]["end_s"] - recs[0]["start_s"] >= 90 * MIN  # the closed exit window
    if sid == "SCN-15":
        assert {r["type"] for r in recs} == {"tsr", "loco_failure"}


def _doc(trace: str, arms: list[str]) -> dict[str, Any]:
    return {
        "scenario": {"id": "SCN-05"},
        "trace_hash": trace,
        "preview": {"id": "WHATIF-SCN-05", "options": [{"id": a, "arm": a, "title": a} for a in arms]},
        "whatif": {"baseline": {}, "arms": arms},
    }


def test_variants_of_the_same_baseline_merge_in_arm_order() -> None:
    merged = whatif.merge_variants(_doc("h", ["DSS"]), _doc("h", ["BL-1"]))
    assert [o["id"] for o in merged["preview"]["options"]] == ["BL-1", "DSS"]
    assert merged["whatif"]["arms"] == ["BL-1", "DSS"]
    again = whatif.merge_variants(
        _doc("h", ["BL-1", "DSS"]), _doc("h", ["DSS"])
    )  # the new arm replaces the old
    assert [o["id"] for o in again["preview"]["options"]] == ["BL-1", "DSS"]
    other = whatif.merge_variants(
        _doc("h1", ["BL-1"]), _doc("h2", ["DSS"])
    )  # another baseline: the new one alone
    assert [o["id"] for o in other["preview"]["options"]] == ["DSS"]


def test_write_lists_the_scenario_next_to_the_days_and_keeps_the_other_arms(tmp_path: Path) -> None:
    (tmp_path / "index.json").write_text(json.dumps({"days": ["2025-08-20"]}), encoding="utf-8")
    path = whatif.write(_doc("h", ["BL-1"]), tmp_path)
    assert path == tmp_path / "SCN-05.json"
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["days"] == [
        "2025-08-20",
        "SCN-05",
    ]
    whatif.write(_doc("h", ["DSS"]), tmp_path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    assert [o["id"] for o in doc["preview"]["options"]] == ["BL-1", "DSS"]
    assert json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))["days"] == [
        "2025-08-20",
        "SCN-05",
    ]
    assert not list(tmp_path.glob("*.tmp"))


def test_a_short_loop_exports_a_chart_document_with_disruptions_and_a_variant(world: run.World) -> None:
    sc = LIBRARY["SCN-05"]
    doc = whatif.build(world, sc, ("BL-1",), span_s=5 * MIN)
    for key in (
        "trains",
        "stations",
        "alerts",
        "mimic",
        "policy",
        "preview",
        "disruptions",
        "scenario",
        "whatif",
    ):
        assert key in doc
    assert doc["format"] == "raildss-chart" and doc["completed"] and doc["scenario"]["id"] == "SCN-05"
    assert [d["id"] for d in doc["disruptions"]] == ["SCN05-DN-CPP-SGDM"]
    (option,) = doc["preview"]["options"]
    assert option["arm"] == "BL-1" and option["id"] == "BL-1" and not doc["preview"]["demo"]
    assert option["kpis"]["baseline"] == doc["whatif"]["baseline"]["kpis"]
    assert set(option["kpis"]["variant"]) == set(option["kpis"]["baseline"])
    assert option["verification"]["status"] in ("PASS", "FAIL") and option["loop"]["plans"] >= 1
    train_ids = {t["id"] for t in doc["trains"]}
    assert set(option["trains"]) <= train_ids  # only trains of the day change
    weighted = [i["weight"] * abs(i["delta_s"]) for i in option["impacts"]]
    assert weighted == sorted(weighted, reverse=True)  # the most affected first


def _cycle(decisions: list[dict[str, Any]], contrasts: list[dict[str, Any]]) -> Any:
    from raildss_orchestrator.loop import CycleRecord

    return CycleRecord(
        cycle=0,
        now_s=36000.0,
        triggers=[],
        plan_id="P-0",
        conflicts=len(decisions),
        blocked_by_g0=0,
        candidates=[],
        baseline={},
        released=[],
        wall_s=0.0,
        decisions=decisions,
        contrasts=contrasts,
    )


def test_the_contrast_of_a_cycle_goes_to_the_card_as_the_most_contested_decision_and_its_why_not() -> None:
    """Issue #174: the card reads ``decision`` and ``why_not`` (DecisionRecord and ContrastRecord)."""
    one = {"trains": ["T1", "T2"], "resource": "ALM~KUK", "options": 1, "precedences": 1}
    two = {"trains": ["T3", "T4"], "resource": "KBM~NWP", "options": 3, "precedences": 2}
    why = {
        "first": "T4",
        "resource": "KBM~NWP",
        "verified": True,
        "reversed": True,
        "cost_min": 7.7,
        "fail": [],
        "answer": "ok",
    }
    got = whatif.contrast_fields(_cycle([one, two], [why]))
    assert got["decision"] == two  # more precedences first
    assert got["why_not"] == [why]
    none = whatif.contrast_fields(_cycle([], []))
    assert none == {"decision": None, "why_not": []}  # tolerated: nothing contested, no re-solve
    only_one = whatif.contrast_fields(_cycle([one], []))
    assert only_one["decision"] == one and only_one["why_not"] == []
