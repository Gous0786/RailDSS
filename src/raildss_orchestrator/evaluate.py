"""Comparison modes of a scenario: BL-0, BL-1, BL-2, BL-3 and BL-5 (spec §12.2, FR-SCN-05/08; issue #168).

One harness runs a scenario under every arm, with the same day, demand, seed and disruptions:

* **BL-0**: the twin, first come first served, no decision support (the loop's own BL-0 run of the day);
* **BL-1**: the decision loop with the greedy planner (controller practice);
* **BL-2**: the DSS with auto-accept, the CP-SAT rolling horizon;
* **BL-3**: BL-2 with the degraded predictor (noisy ETAs, ``pred.degraded``);
* **BL-5**: the historical reference of the scenario's freight day (the Aug 2025 freight log, FR-KPI-11),
  where it exists: freight detention only, the log has no passenger actuals.

The loop arms plan in the scenario's window (:func:`raildss_orchestrator.scenarios.with_loop`); their KPIs are
the day's (FR-KPI-05) plus the conflict (D1, D2) and alert (H1, H2) KPIs of the window. The result is an
evaluation export (``docs/kpi/evaluation-export.md``, schema ``docs/kpi/evaluation.schema.json``) and a
Markdown report with the KPI deltas.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from raildss_twin import run, scenario
from raildss_twin.scenario import Scenario

from .scenarios import LOOP_ARMS, SECONDS_PER_HOUR, SECONDS_PER_MIN, SPAN_S, loopable, with_loop

ARMS = ("BL-0", "BL-1", "BL-2", "BL-3", "BL-5")
LOOPS = ("BL-1", "BL-2", "BL-3")
KPI_NAME = re.compile(r"^[A-Z][0-9]+_[A-Za-z0-9_]+$")  # the export's KPI names
REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "kpi" / "historical-reference-ktv-psa.json"
SCHEMA_VERSION = 1
SHOWN = (
    "A5_weighted_delay_train_min",
    "A2_section_punctuality_pct",
    "A4_mean_arrival_delay_min",
    "A6_mean_exit_delay_min",
    "A6_p90_exit_delay_min",
    "B1_trains_exited",
    "C2_freight_detention_h",
    "F1_unplanned_stops",
    "D1_median_conflict_lead_min",
    "D2_resolved_before_occurrence_pct",
    "H1_alerts_per_controller_hour",
    "H2_flood_events",
)
WINDOW_KPIS = (  # measured in the loop window, not by BL-0
    "D1_median_conflict_lead_min",
    "D2_resolved_before_occurrence_pct",
    "H1_alerts_per_controller_hour",
    "H2_flood_events",
)
MEANING = {
    "BL-0": "no decision support",
    "BL-1": "loop, greedy planner",
    "BL-2": "DSS, CP-SAT",
    "BL-3": "DSS, degraded predictor",
    "BL-5": "historical reference",
}
NOTES = {
    "BL-0": "The twin with the scripted disruptions, first come first served, no decision support.",
    "BL-1": "The decision loop with the greedy planner (controller practice).",
    "BL-2": "The DSS with auto-accept: the CP-SAT rolling horizon.",
    "BL-3": "The DSS with the degraded predictor (pred.degraded noise on every ETA).",
    "BL-5": "The historical reference (K1): the freight log of the day; it has freight only, no passenger "
    "actuals.",
}


def _kpis(values: dict[str, Any]) -> dict[str, float | None]:
    return {k: (None if v is None else float(v)) for k, v in values.items() if KPI_NAME.match(k)}


def _freight_day(sc: Scenario) -> str | None:
    """The calendar day of the scenario's freight day instance (``aug25-day-07`` -> ``2025-08-07``)."""
    m = re.fullmatch(r"aug25-day-(\d{2})", sc.freight_day or "")
    return f"2025-08-{m.group(1)}" if m else None


def reference_row(sc: Scenario, reference: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """BL-5 for the scenario's freight day, or None when the log has no such day."""
    ref = reference if reference is not None else json.loads(REFERENCE.read_text(encoding="utf-8"))
    day = _freight_day(sc)
    delay = ref.get("delay_indicators", {}).get("by_day", {}).get(day) if day else None
    if delay is None:
        return None
    kpis: dict[str, float | None] = dict.fromkeys(SHOWN)
    kpis["C2_freight_detention_h"] = float(delay["detention_hours"])
    return {
        "scenario": sc.id,
        "arm": "BL-5",
        "kpis": kpis,
        "plans_verified": None,
        "notes": f"{NOTES['BL-5']} Freight log of {day}.",
    }


def evaluate(
    world: run.World, sc: Scenario, *, arms: Sequence[str] = ARMS, span_s: float = SPAN_S
) -> list[dict[str, Any]]:
    """The rows of one scenario, one per arm (FR-SCN-05)."""
    rows: list[dict[str, Any]] = []
    base: dict[str, Any] | None = None
    looped = loopable(sc) or not sc.disruptions
    for arm in (a for a in arms if a in LOOPS and looped):
        summary, comp = with_loop(world, sc, arm, span_s)
        base = base or comp["arms"]["BL-0"]  # every loop arm compares with the same BL-0 run
        kpis = _kpis(comp["arms"][arm]) | _kpis(comp.get("conflicts", {})) | _kpis(comp.get("alerts", {}))
        built = sum(len(cy.candidates) for cy in summary.cycles)
        verified = sum(c.verified for cy in summary.cycles for c in cy.candidates)
        safety = comp.get("safety", {})
        rows.append(
            {
                "scenario": sc.id,
                "title": sc.title,
                "arm": arm,
                "kpis": kpis,
                "plans_verified": {"verified": verified, "built": built},
                "notes": f"{NOTES[arm]} Window {summary.from_s / SECONDS_PER_HOUR:.2f}-"
                f"{summary.to_s / SECONDS_PER_HOUR:.2f} h; "
                f"executed {safety.get('head_on', 0)} head-on, {safety.get('deadlocks', 0)} deadlocks.",
            }
        )
    if "BL-0" in arms:
        if base is None:  # no loop arm: the day under BL-0 with the scenario's disruptions
            from .scenarios import bl0

            base = bl0(world, sc).kpis
        rows.insert(
            0,
            {
                "scenario": sc.id,
                "title": sc.title,
                "arm": "BL-0",
                "kpis": _kpis(base) | dict.fromkeys(WINDOW_KPIS),
                "plans_verified": None,
                "notes": NOTES["BL-0"],
            },
        )
    if "BL-5" in arms:
        ref = reference_row(sc)
        if ref is not None:
            rows.append(ref)
    return rows


def export(
    rows: list[dict[str, Any]], world: run.World, *, scenario_set: str, generated_by: str, span_s: float
) -> dict[str, Any]:
    """An evaluation export (``docs/kpi/evaluation.schema.json``)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "network_version": world.layout.network_version,
            "register_version": world.register.version,
            "scenario_set": scenario_set,
            "seed": 0,
            "generated_by": generated_by,
            "generated_on": dt.date.today().isoformat(),
            "sample": False,
            "notes": "Each scenario's own seed and day instance (scenario files); every arm of a scenario "
            f"runs the same day, demand and disruptions. Loop window {span_s / SECONDS_PER_MIN:g} min.",
        },
        "rows": rows,
    }


def markdown(sc: Scenario, rows: list[dict[str, Any]], generated_by: str) -> str:
    """The report of one scenario: KPIs by arm, differences from BL-0 and BL-2 against BL-1."""
    arms = [r["arm"] for r in rows]
    by = {r["arm"]: r["kpis"] for r in rows}

    def cell(v: float | None) -> str:
        return "-" if v is None else f"{v:g}"

    def diff(a: float | None, b: float | None) -> str:
        return "-" if a is None or b is None else f"{a - b:+.2f}"

    deltas = [a for a in arms if a not in ("BL-0", "BL-5")] if "BL-0" in by else []
    pair = ["BL-2 - BL-1"] if {"BL-1", "BL-2"} <= by.keys() else []
    head = [*arms, *(f"{a} - BL-0" for a in deltas), *pair]
    out = [
        f"# {sc.id}: {sc.title} (evaluation, issue #168)",
        "",
        f"Generated by `{generated_by}`.",
        "",
        "| Arm | Meaning | Plans verified | Notes |",
        "|---|---|---:|---|",
    ]
    for r in rows:
        pv = r.get("plans_verified")
        verified = f"{pv['verified']} / {pv['built']}" if pv else "-"
        out.append(f"| {r['arm']} | {MEANING[r['arm']]} | {verified} | {r.get('notes', '')} |")
    out += ["", "| KPI | " + " | ".join(head) + " |", "|---|" + "---:|" * len(head)]
    for k in SHOWN:
        vals = [by[a].get(k) for a in arms]
        row = [cell(v) for v in vals] + [diff(by[a].get(k), by["BL-0"].get(k)) for a in deltas]
        if {"BL-1", "BL-2"} <= by.keys():
            row.append(diff(by["BL-2"].get(k), by["BL-1"].get(k)))
        out.append(f"| {k} | " + " | ".join(row) + " |")
    return "\n".join(out) + "\n"


def run_scenarios(
    world: run.World, ids: Sequence[str], *, arms: Sequence[str] = ARMS, span_s: float = SPAN_S
) -> list[tuple[Scenario, list[dict[str, Any]]]]:
    library = {s.id: s for s in scenario.load_library_v1() if s.executable}
    missing = [i for i in ids if i not in library]
    if missing:
        raise ValueError(f"not executable scenarios of library v1: {', '.join(missing)}")
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        raise ValueError(f"unknown arms {unknown}; the arms are {', '.join(ARMS)}")
    assert set(LOOPS) <= set(LOOP_ARMS)
    return [(library[i], evaluate(world, library[i], arms=arms, span_s=span_s)) for i in ids]
