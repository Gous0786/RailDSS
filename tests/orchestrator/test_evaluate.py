"""The comparison modes of a scenario on a short window (issue #168): BL-0, BL-1, BL-2, BL-3 and BL-5, an
evaluation export that validates against ``docs/kpi/evaluation.schema.json`` and a Markdown report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from raildss_orchestrator import evaluate
from raildss_orchestrator.__main__ import main
from raildss_twin import run, scenario

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "docs" / "kpi" / "evaluation.schema.json").read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
SPAN_S = 240.0  # two cycles of the loop


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def _sc(sid: str) -> Any:
    return next(s for s in scenario.load_library_v1() if s.id == sid)


def _valid(doc: dict[str, Any]) -> None:
    errors = [e.message for e in VALIDATOR.iter_errors(doc)]
    assert errors == []


def test_bl0_bl1_and_the_historical_reference_on_a_short_window(world: run.World) -> None:
    sc = _sc("SCN-10")
    rows = evaluate.evaluate(world, sc, arms=("BL-0", "BL-1", "BL-5"), span_s=SPAN_S)
    assert [r["arm"] for r in rows] == ["BL-0", "BL-1", "BL-5"]
    by = {r["arm"]: r for r in rows}
    assert by["BL-1"]["plans_verified"]["built"] >= 1 and by["BL-0"]["plans_verified"] is None
    assert by["BL-1"]["kpis"]["D2_resolved_before_occurrence_pct"] is not None  # window KPIs of the loop
    assert by["BL-0"]["kpis"]["D2_resolved_before_occurrence_pct"] is None
    assert by["BL-0"]["kpis"]["A5_weighted_delay_train_min"] is not None  # the loop's own BL-0 run
    assert by["BL-5"]["kpis"]["C2_freight_detention_h"] is not None  # the freight log of 17 Aug 2025
    assert by["BL-5"]["kpis"]["A5_weighted_delay_train_min"] is None
    doc = evaluate.export(rows, world, scenario_set="SCN-10", generated_by="test", span_s=SPAN_S)
    _valid(doc)
    text = evaluate.markdown(sc, rows, "test")
    assert "| A5_weighted_delay_train_min |" in text and "BL-1 - BL-0" in text


def test_a_scenario_without_a_freight_log_day_has_no_bl5_row() -> None:
    sc = _sc("SCN-10").model_copy(update={"freight_day": None, "nhpp_seed": 3})
    assert evaluate.reference_row(sc) is None


def test_the_cli_writes_a_valid_export_and_report(tmp_path: Path) -> None:
    out = tmp_path / "eval"
    # BL-3 runs the CP-SAT loop with the degraded predictor (the BL-2 code path with noisy ETAs)
    argv = ["evaluate", "SCN-07", "--arm", "BL-0", "--arm", "BL-3", "--span-min", "2", "--out-dir", str(out)]
    assert main(argv) == 0
    doc = json.loads((out / "SCN-07.json").read_text(encoding="utf-8"))
    _valid(doc)
    assert [r["arm"] for r in doc["rows"]] == ["BL-0", "BL-3"]
    assert doc["rows"][1]["plans_verified"]["built"] >= 1
    md = (out / "SCN-07.md").read_text(encoding="utf-8")
    assert "BL-3 - BL-0" in md and "| A5_weighted_delay_train_min |" in md
    assert evaluate.ARMS == ("BL-0", "BL-1", "BL-2", "BL-3", "BL-5")  # the default: every arm
