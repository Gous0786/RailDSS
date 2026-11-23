"""The evaluation export schema and its sample fixture (issue #173; the runner is #168)."""

from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from raildss_dss import alert_kpi, conflict_kpi
from raildss_dss.kpi import compute

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "docs" / "kpi" / "evaluation.schema.json"
DOC = ROOT / "docs" / "kpi" / "evaluation-export.md"
FIXTURE = ROOT / "web" / "public" / "data" / "evaluation.json"

schema: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
fixture: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
KPI_PROPS = set(schema["$defs"]["row"]["properties"]["kpis"]["properties"])
ARMS = {"BL-0", "BL-1", "BL-2", "BL-3", "BL-5", "DSS"}


def _errors(doc: dict[str, Any]) -> list[str]:
    return [e.message for e in validator.iter_errors(doc)]


def test_the_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"] == {"const": 1}
    assert set(schema["$defs"]["row"]["properties"]["arm"]["enum"]) == ARMS


def test_the_sample_fixture_validates_and_is_marked_as_sample() -> None:
    assert _errors(fixture) == []
    assert fixture["run"]["sample"] is True  # the view shows the sample-data banner
    assert "SAMPLE DATA" in fixture["run"]["notes"]
    dt.date.fromisoformat(fixture["run"]["generated_on"])


def test_the_fixture_has_one_row_per_scenario_and_arm_and_covers_the_arms() -> None:
    pairs = [(r["scenario"], r["arm"]) for r in fixture["rows"]]
    assert len(pairs) == len(set(pairs))
    assert {r["arm"] for r in fixture["rows"]} == ARMS - {"DSS"}  # the sample uses the spec name BL-2
    assert len({r["scenario"] for r in fixture["rows"]}) >= 3
    assert {k for r in fixture["rows"] for k in r["kpis"]} <= KPI_PROPS
    assert {
        "D1_median_conflict_lead_min",
        "D2_resolved_before_occurrence_pct",
        "H1_alerts_per_controller_hour",
    } <= {k for r in fixture["rows"] for k in r["kpis"]}


def test_the_schema_rejects_malformed_exports() -> None:
    def bad(edit: Any) -> list[str]:
        doc = copy.deepcopy(fixture)
        edit(doc)
        return _errors(doc)

    assert bad(lambda d: d.pop("run"))
    assert bad(lambda d: d["run"].pop("register_version"))
    assert bad(lambda d: d.update(schema_version=2))
    assert bad(lambda d: d.update(rows=[]))
    assert bad(lambda d: d["rows"][0].update(arm="BL-9"))
    assert bad(lambda d: d["rows"][0].pop("kpis"))
    assert bad(lambda d: d["rows"][0].update(kpis={}))
    assert bad(lambda d: d["rows"][0]["kpis"].update(A5_weighted_delay_train_min="high"))
    assert bad(lambda d: d["rows"][0]["kpis"].update(delay=3))  # not a KPI name
    assert bad(lambda d: d["rows"][1].update(plans_verified={"verified": 1}))
    assert bad(lambda d: d["rows"][0].update(surprise=1))
    assert bad(lambda d: d["run"].update(generated_on="yesterday"))
    ok = copy.deepcopy(fixture)
    ok["rows"][0]["kpis"]["X9_future_kpi"] = 1.5  # a KPI the schema does not list yet is allowed
    ok["rows"][0]["plans_verified"] = None
    assert _errors(ok) == []


def test_the_kpi_names_are_the_ones_the_code_emits() -> None:
    engine = compute(
        [], {}, dt.datetime(2025, 8, 20, tzinfo=dt.UTC), scope="s", arm="a", network_version="n"
    ).kpis
    assert set(engine) <= KPI_PROPS, set(engine) - KPI_PROPS
    conflicts = conflict_kpi.ConflictKpis(1, 20.0, 1, 0, 0, 100.0, 0).as_dict()
    alerts = alert_kpi.AlertKpis(1, 1.0, 1, 1.0, 6.0, 0, 1, {}, 1, 0).as_dict()
    for name in ("D1_median_conflict_lead_min", "D2_resolved_before_occurrence_pct"):
        assert name in conflicts and name in KPI_PROPS
    for name in ("H1_alerts_per_controller_hour", "H2_flood_events"):
        assert name in alerts and name in KPI_PROPS


@pytest.mark.parametrize("name", sorted(KPI_PROPS))
def test_every_documented_kpi_is_in_the_doc(name: str) -> None:
    assert f"`{name}`" in DOC.read_text(encoding="utf-8").replace("`, `", "` `") or name in DOC.read_text(
        encoding="utf-8"
    )
