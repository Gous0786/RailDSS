"""Data-quality report and baseline gate tests (issue #17 acceptance criteria)."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

import pytest

import raildss_params
from raildss_etl import dq


@pytest.fixture(scope="module")
def stages() -> dq.Stages:
    return dq.run_stages()


@pytest.fixture(scope="module")
def report(stages: dq.Stages) -> dq.DqReport:
    return dq.build_report(stages=stages)


def test_all_dq_14_to_22_checks_pass(report: dq.DqReport) -> None:
    assert [c.id for c in report.checks] == [f"DQ-{n}" for n in range(14, 23)]
    assert all(c.status == "PASS" for c in report.checks), [c for c in report.checks if c.status != "PASS"]
    assert report.ok


def test_report_matches_the_committed_baseline(report: dq.DqReport) -> None:
    assert dq.diff_against_baseline(report) == []
    assert dq.to_json(report) == dq.BASELINE_JSON.read_text(encoding="utf-8")
    assert dq.to_markdown(report) == dq.BASELINE_MD.read_text(encoding="utf-8")


def test_report_covers_the_required_sections(report: dq.DqReport) -> None:
    assert report.row_counts["STATION"] == {
        "raw": 22,
        "blank": 0,
        "repeated_header": 0,
        "duplicate": 0,
        "clean": 22,
        "expected": 22,
    }
    assert (
        report.row_counts["BLOCKSCTN"]["duplicate"] > 0
        and report.row_counts["BLOCKSCTN"]["repeated_header"] == 1
    )
    assert report.fixes["psa_swap"]["ops"] == ["PSA"] and report.fixes["psa_swap"]["lines"] == 10
    assert report.fixes["freight_recomputed"]["block_hrs_agreement"] >= 0.95
    assert any("PSA/7" in c for c in report.anomalies["direction_conflicts"])
    assert any("PDU line 5" in n for n in report.anomalies["pdu_line_5"])
    assert any(s.startswith("CPP/1") for s in report.anomalies["short_lines"])
    assert report.anomalies["negative_dwell_rows"] == 2 and report.anomalies["stabled_loads"] > 0
    assert {"MANCAPACITY", "MANSTARTBUFFER", "MANENDBUFFER"} <= set(report.ignored_fields["STTNLINE"])


def test_report_is_deterministic_and_has_no_timestamps(report: dq.DqReport) -> None:
    text = dq.to_json(report)
    assert text == dq.to_json(dq.DqReport.model_validate(json.loads(text)))
    assert "generated_at" not in text
    assert not re.search(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}", text)  # no run time


def test_any_changed_count_or_finding_fails_the_gate(
    report: dq.DqReport, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    baseline = json.loads(dq.to_json(report))
    baseline["row_counts"]["STATION"]["clean"] += 1  # a count drifted
    baseline["anomalies"]["short_lines"].append("XXX/9 (loop, CSR 1 m)")  # a finding disappeared
    path = tmp_path / "baseline.json"
    path.write_text(
        json.dumps(baseline, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    monkeypatch.setattr(dq, "BASELINE_JSON", path)
    diff = "\n".join(dq.diff_against_baseline(report))
    assert '"clean": 23' in diff and "XXX/9" in diff
    monkeypatch.setattr(dq, "BASELINE_JSON", tmp_path / "missing.json")
    assert "no baseline" in dq.diff_against_baseline(report)[0]


def test_a_failing_check_makes_the_report_fail(report: dq.DqReport) -> None:
    broken = report.model_copy(
        update={"checks": [*report.checks[:-1], report.checks[-1].model_copy(update={"status": "FAIL"})]}
    )
    assert not broken.ok


# ------------------------------------------------------ the gate and the register hash (DR-0002 F05)


def test_register_version_is_recorded_separately_not_in_the_diffed_content(
    report: dq.DqReport, tmp_path: Path
) -> None:
    assert report.register_version  # known to the build ...
    assert "register_version" not in dq.to_json(report)  # ... but not report content
    assert report.register_version not in dq.to_markdown(report)
    assert "register_version" not in report.model_dump(mode="json")
    dq.write_report(report, tmp_path)
    meta = json.loads((tmp_path / dq.META_FILE).read_text(encoding="utf-8"))
    assert meta["register_version"] == report.register_version


def test_an_unrelated_parameter_edit_does_not_fail_the_gate(
    stages: dq.Stages, report: dq.DqReport, tmp_path: Path
) -> None:
    """Editing a parameter the ETL does not read changes the register hash, not the report."""
    text = Path(raildss_params.default_path()).read_text(encoding="utf-8")
    key = "  - key: ver.buffer_warn_min\n    value: 2\n"
    assert key in text
    edited = tmp_path / "parameters.yaml"
    edited.write_text(text.replace(key, key.replace("value: 2", "value: 3")), encoding="utf-8")
    altered = raildss_params.load(edited)
    assert altered.version != stages.register.version
    after = dq.build_report(stages=dataclasses.replace(stages, register=altered))
    assert after.register_version == altered.version
    assert dq.diff_against_baseline(after) == []
    assert dq.to_json(after) == dq.to_json(report)


def test_a_change_to_a_dq_fact_still_fails_the_gate(report: dq.DqReport) -> None:
    counts = {**report.entity_counts, "services": report.entity_counts["services"] + 1}
    assert dq.diff_against_baseline(report.model_copy(update={"entity_counts": counts})) != []
    checks = [report.checks[0].model_copy(update={"status": "FAIL"}), *report.checks[1:]]
    changed = report.model_copy(update={"checks": checks})
    assert dq.diff_against_baseline(changed) != [] and not changed.ok
