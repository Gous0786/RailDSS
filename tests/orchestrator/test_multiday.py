"""Multi-day replay of BL-1 and BL-2 on real days (issue #169, FV-06): the aggregation on hand-made runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from raildss_orchestrator import multiday
from raildss_twin import run

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "docs" / "kpi" / "evaluation.schema.json").read_text(encoding="utf-8"))


def _run(day: str, arm: str, a5: float, base_a5: float, **extra: float) -> dict[str, Any]:
    return {
        "day": day,
        "arm": arm,
        "window_h": [6.0, 12.0],
        "bl0": {
            "A5_weighted_delay_train_min": base_a5,
            "A2_section_punctuality_pct": 60.0,
            "C2_freight_detention_h": 10.0,
        },
        "kpis": {
            "A5_weighted_delay_train_min": a5,
            "A2_section_punctuality_pct": 62.0,
            "C2_freight_detention_h": 11.0,
            "D1_median_conflict_lead_min": 30.0,
            "D2_resolved_before_occurrence_pct": 60.0,
            "H1_alerts_per_controller_hour": 40.0,
            "H2_flood_events": 5.0,
            **extra,
        },
        "plans_verified": {"verified": 9, "built": 10},
        "plans": 10,
        "recommendations_accepted": 4,
        "orders": 6,
        "head_on": 0,
        "deadlocks": 0,
        "wall_s": 1800,
    }


# A5 against BL-0 (negative is better): BL-1 -100 / -200 / +50, BL-2 -300 / -100 / -50
RESULTS = [
    _run("2025-08-12", "BL-1", 900.0, 1000.0),
    _run("2025-08-12", "BL-2", 700.0, 1000.0),
    _run("2025-08-20", "BL-1", 1800.0, 2000.0),
    _run("2025-08-20", "BL-2", 1900.0, 2000.0),
    _run("2025-08-22", "BL-1", 3050.0, 3000.0),
    _run("2025-08-22", "BL-2", 2950.0, 3000.0),
]


def test_a_real_day_is_the_nominal_template_with_its_day_and_freight_day_and_no_disruption() -> None:
    sc = multiday.day_scenario("2025-08-12")
    assert str(sc.wtt_date) == "2025-08-12" and sc.freight_day == "aug25-day-12" and sc.disruptions == []
    assert (
        multiday.DAYS[:1] == ("2025-08-05",)
        and "2025-08-12" in multiday.DAYS
        and "2025-08-20" in multiday.DAYS
    )
    assert len(multiday.DAYS) >= 5  # the issue asks for at least five real days
    assert multiday.FROM_H == 6.0 and multiday.SPAN_H == 6.0  # a 6 h window


def test_the_difference_per_day_is_against_bl0_or_bl1() -> None:
    k = "A5_weighted_delay_train_min"
    assert multiday.day_deltas(RESULTS, "BL-1", k) == {
        "2025-08-12": -100.0,
        "2025-08-20": -200.0,
        "2025-08-22": 50.0,
    }
    assert multiday.day_deltas(RESULTS, "BL-2", k) == {
        "2025-08-12": -300.0,
        "2025-08-20": -100.0,
        "2025-08-22": -50.0,
    }
    assert multiday.day_deltas(RESULTS, "BL-2", k, "BL-1") == {
        "2025-08-12": -200.0,
        "2025-08-20": 100.0,
        "2025-08-22": -100.0,
    }
    assert (
        multiday.day_deltas(RESULTS, "BL-2", "D1_median_conflict_lead_min") == {}
    )  # BL-0 has none: no difference
    assert multiday.day_deltas(RESULTS[:1], "BL-2", k) == {}  # no BL-2 run


def test_the_spread_counts_the_days_on_which_the_kpi_improves_or_worsens() -> None:
    s = multiday.spread(
        multiday.day_deltas(RESULTS, "BL-1", "A5_weighted_delay_train_min"), "A5_weighted_delay_train_min"
    )
    assert (s["n"], s["improved"], s["worsened"]) == (3, 2, 1)
    assert s["mean"] == pytest.approx((-100 - 200 + 50) / 3)
    assert s["std"] == pytest.approx(125.83, abs=0.01) and (s["min"], s["max"]) == (-200.0, 50.0)
    higher = multiday.spread({"a": 2.0, "b": -1.0}, "A2_section_punctuality_pct")  # higher is better
    assert (higher["improved"], higher["worsened"]) == (1, 1)
    one = multiday.spread({"a": 1.0}, "A5_weighted_delay_train_min")
    assert one["std"] is None and one["n"] == 1
    empty = multiday.spread({}, "A5_weighted_delay_train_min")
    assert empty["mean"] is None and empty["min"] is None


def test_the_report_has_the_runs_the_days_and_the_spread() -> None:
    text = multiday.markdown(RESULTS, "cmd")
    assert text.startswith("# Multi-day replay")
    assert "3 real days (2025-08-12, 2025-08-20, 2025-08-22)" in text and "6-12 h" in text
    assert "| 2025-08-12 | BL-1 | 10 (9 / 10) | 4 | 6 | 0 | 30 min |" in text
    assert (
        "| 2025-08-12 | A5_weighted_delay_train_min | 1000 | 900 | 700 | -100.00 | -300.00 | -200.00 |"
        in text
    )
    spread = [
        line for line in text.splitlines() if line.startswith("| A5_weighted_delay_train_min | BL-1 - BL-0")
    ]
    assert spread and spread[0].endswith("| 2 | 1 |")  # improves on 2 days, worsens on 1
    assert any(line.startswith("| A5_weighted_delay_train_min | BL-2 - BL-1") for line in text.splitlines())
    assert "-" in next(
        line for line in text.splitlines() if "D1_median_conflict_lead_min" in line
    )  # BL-0 has no D1


def test_a_single_arm_report_has_no_bl2_minus_bl1_column() -> None:
    text = multiday.markdown([r for r in RESULTS if r["arm"] == "BL-1"])
    assert "BL-2 - BL-1" not in text and "BL-1 - BL-0" in text


def test_the_runs_are_written_and_read_one_file_each(tmp_path: Path) -> None:
    for r in RESULTS:
        multiday.write_result(tmp_path, r)
    assert sorted(p.name for p in tmp_path.glob("*.json")) == [f"{r['day']}.{r['arm']}.json" for r in RESULTS]
    assert multiday.load_results(tmp_path) == sorted(RESULTS, key=lambda r: (r["day"], r["arm"]))


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def test_the_export_validates_against_the_evaluation_schema(world: run.World) -> None:
    doc = multiday.export(RESULTS, world, generated_by="cmd")
    assert (
        list(
            Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER).iter_errors(doc)
        )
        == []
    )
    assert doc["run"]["sample"] is False and doc["run"]["window"] == {"from_h": 6.0, "to_h": 12.0}
    pairs = [(r["scenario"], r["arm"]) for r in doc["rows"]]
    assert len(pairs) == len(set(pairs))
    assert [a for s, a in pairs if s == "day 2025-08-12"][:3] == ["BL-0", "BL-1", "BL-2"]
    bl0 = next(r for r in doc["rows"] if r["scenario"] == "day 2025-08-12" and r["arm"] == "BL-0")
    assert (
        bl0["kpis"]["A5_weighted_delay_train_min"] == 1000.0
        and bl0["kpis"]["D2_resolved_before_occurrence_pct"] is None
    )
    assert bl0["plans_verified"] is None
    history = [r for r in doc["rows"] if r["arm"] == "BL-5"]
    assert {r["scenario"] for r in history} == {"day 2025-08-12", "day 2025-08-20", "day 2025-08-22"}
    assert all(r["kpis"]["C2_freight_detention_h"] > 0 for r in history)


def test_the_findings_state_the_sign_of_a5_on_how_many_days_and_where_it_does_not_hold() -> None:
    lines = multiday.findings(RESULTS)
    bl1, bl2, pair = lines[0], lines[1], lines[2]
    assert bl1.startswith("**BL-1 against BL-0:**") and "falls on 2 of 3 days" in bl1
    assert "It rises on 2025-08-22 (+50.0)" in bl1  # the day where the sign does not hold is named
    assert "falls on 3 of 3 days" in bl2 and "It rises" not in bl2
    assert "lower with BL-2 on 2 of 3 days" in pair and "BL-2 beats the greedy loop on A5 on average" in pair
    assert any(
        line.startswith("Share of candidate plans that passed G0-G3: BL-1 90 %, BL-2 90 %") for line in lines
    )


def test_the_findings_say_when_bl2_does_not_beat_bl1() -> None:
    worse = [
        dict(
            r,
            kpis=dict(
                r["kpis"],
                A5_weighted_delay_train_min=r["bl0"]["A5_weighted_delay_train_min"]
                - (5 if r["arm"] == "BL-2" else 50),
            ),
        )
        for r in RESULTS
    ]
    assert any("does not beat the greedy loop on A5" in line for line in multiday.findings(worse))


def test_the_report_ends_with_the_findings() -> None:
    text = multiday.markdown(RESULTS, "cmd")
    assert "## Reading the results" in text
    assert text.index("## Spread over the days") < text.index("## Reading the results")
    assert "- **BL-1 against BL-0:**" in text
