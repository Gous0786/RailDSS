"""Multi-day replay of the BL-1 / BL-2 comparison on real days over a long window (FV-06; issue #169).

The M1 face-validity review (``docs/review/m1-face-validity.md``, FV-06) compared the DSS with BL-0 on one day
and a two-hour window. This runs the decision loop of both arms (BL-1: greedy; BL-2: the CP-SAT rolling
horizon) over a **6 h window** (06:00-12:00 by default) of each of five real days, against BL-0 on the same
day: the day, the demand (the real freight day) and the seed are the same for every arm.

One run is one (day, arm) and takes well under two hours; it writes its own JSON file, so runs can go in
parallel and be repeated one by one. ``report`` then reads every file and writes

* ``docs/kpi/multi-day-replay.md``: the KPIs per day and arm, the differences from BL-0 and of BL-2 from
  BL-1, and their spread over the days (mean, standard deviation, range, on how many days the sign holds);
* an evaluation export (``docs/kpi/evaluation-export.md``) that the management view of the web app reads.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from raildss_twin import run, scenario
from raildss_twin.scenario import Scenario

from .evaluate import KPI_NAME, NOTES, SCHEMA_VERSION, SHOWN, WINDOW_KPIS, reference_row
from .scenarios import SECONDS_PER_HOUR, SECONDS_PER_MIN, with_loop

DAYS = ("2025-08-05", "2025-08-12", "2025-08-15", "2025-08-20", "2025-08-22")
FROM_H = 6.0
SPAN_H = 6.0
TEMPLATE = "SCN-22"  # a real-freight-day scenario without disruptions: the day instance is replaced
LABEL_COLUMNS = 2  # day and KPI: the left-aligned columns of the per-day table
LOOP_ARMS = ("BL-1", "BL-2")
OUT_DIR = Path("docs/kpi/multi-day")
REPORT = Path("docs/kpi/multi-day-replay.md")
BETTER = {  # the direction in which a KPI improves
    "A5_weighted_delay_train_min": "lower",
    "A2_section_punctuality_pct": "higher",
    "A4_mean_arrival_delay_min": "lower",
    "A6_mean_exit_delay_min": "lower",
    "A6_p90_exit_delay_min": "lower",
    "C2_freight_detention_h": "lower",
    "F1_unplanned_stops": "lower",
    "D1_median_conflict_lead_min": "higher",
    "D2_resolved_before_occurrence_pct": "higher",
    "H1_alerts_per_controller_hour": "lower",
    "H2_flood_events": "lower",
}


def label(day: str) -> str:
    return f"day {day}"


def day_scenario(day: str) -> Scenario:
    """A scenario of the real day: the nominal template with the day, its freight day and no disruption."""
    template = next(s for s in scenario.load_library_v1() if s.id == TEMPLATE)
    date = dt.date.fromisoformat(day)
    return template.model_copy(
        update={"wtt_date": date, "freight_day": f"aug25-day-{date.day:02d}", "title": f"Real day {day}"}
    )


def _kpis(values: dict[str, Any]) -> dict[str, float | None]:
    return {k: (None if v is None else float(v)) for k, v in values.items() if KPI_NAME.match(k)}


def run_one(
    world: run.World, day: str, arm: str, *, from_h: float = FROM_H, span_h: float = SPAN_H
) -> dict[str, Any]:
    """The loop of one arm over the window of one day, and the day's KPIs against BL-0."""
    sc = day_scenario(day)
    t0 = time.perf_counter()
    summary, comp = with_loop(world, sc, arm, span_h * SECONDS_PER_HOUR, from_h * SECONDS_PER_HOUR)
    verified = sum(c.verified for cy in summary.cycles for c in cy.candidates)
    built = sum(len(cy.candidates) for cy in summary.cycles)
    safety = comp.get("safety", {})
    return {
        "day": day,
        "arm": arm,
        "window_h": [from_h, from_h + span_h],
        "bl0": _kpis(comp["arms"]["BL-0"]),
        "kpis": _kpis(comp["arms"][arm]) | _kpis(comp.get("conflicts", {})) | _kpis(comp.get("alerts", {})),
        "plans_verified": {"verified": verified, "built": built},
        "plans": len(summary.cycles),
        "recommendations_accepted": summary.accepted,
        "orders": summary.orders,
        "head_on": int(safety.get("head_on", 0)),
        "deadlocks": int(safety.get("deadlocks", 0)),
        "wall_s": round(time.perf_counter() - t0),
    }


def result_path(out_dir: Path, day: str, arm: str) -> Path:
    return out_dir / f"{day}.{arm}.json"


def write_result(out_dir: Path, result: dict[str, Any]) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = result_path(out_dir, result["day"], result["arm"])
    path.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    return path


def load_results(out_dir: Path) -> list[dict[str, Any]]:
    return sorted(
        (json.loads(p.read_text(encoding="utf-8")) for p in out_dir.glob("*.*.json")),
        key=lambda r: (r["day"], r["arm"]),
    )


# ---------------------------------------------------------------------------------------- aggregation
def _by_day(results: Sequence[dict[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for r in results:
        out.setdefault(r["day"], {})[r["arm"]] = r
    return dict(sorted(out.items()))


def _delta(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None else a - b


def day_deltas(results: Sequence[dict[str, Any]], arm: str, key: str, base: str = "BL-0") -> dict[str, float]:
    """KPI of ``arm`` minus that of ``base`` per day, over the days with both (``BL-0`` is the day's own)."""
    out: dict[str, float] = {}
    for day, arms in _by_day(results).items():
        if arm not in arms:
            continue
        if base == "BL-0":
            ref = arms[arm]["bl0"].get(key)
        else:
            ref = arms[base]["kpis"].get(key) if base in arms else None
        d = _delta(arms[arm]["kpis"].get(key), ref)
        if d is not None:
            out[day] = d
    return out


def spread(deltas: dict[str, float], key: str) -> dict[str, Any]:
    """Mean, standard deviation, range and on how many days the KPI improves."""
    vals = list(deltas.values())
    better = BETTER.get(key)
    improved = sum(1 for v in vals if (v < 0 if better == "lower" else v > 0)) if better else 0
    worsened = sum(1 for v in vals if (v > 0 if better == "lower" else v < 0)) if better else 0
    return {
        "n": len(vals),
        "mean": statistics.fmean(vals) if vals else None,
        "std": statistics.stdev(vals) if len(vals) > 1 else None,
        "min": min(vals) if vals else None,
        "max": max(vals) if vals else None,
        "improved": improved,
        "worsened": worsened,
    }


def _cell(x: float | None) -> str:
    return "-" if x is None else f"{round(x, 2):g}"


def _sign(x: float | None) -> str:
    return "-" if x is None else f"{x:+.2f}"


REPORT_COMMAND = "python -m raildss_orchestrator multiday report"


def _days(deltas: dict[str, float], worse: bool) -> str:
    return ", ".join(f"{d} ({v:+.1f})" for d, v in deltas.items() if (v > 0) == worse and v != 0)


def findings(results: Sequence[dict[str, Any]]) -> list[str]:
    """The sentences of the report about the sign of the A5 difference, from the numbers (issue #169)."""
    key = "A5_weighted_delay_train_min"
    out: list[str] = []
    for arm in LOOP_ARMS:
        d = day_deltas(results, arm, key)
        if not d:
            continue
        s = spread(d, key)
        text = (
            f"**{arm} against BL-0:** the weighted delay (A5) falls on {s['improved']} of {s['n']} days "
            f"(mean {s['mean']:+.1f} train-min, standard deviation {s['std'] or 0:.1f}, "
            f"range {s['min']:+.1f} to {s['max']:+.1f})."
        )
        rose = _days(d, True)
        if rose:
            text += f" It rises on {rose}."
            base = {day: by[arm]["bl0"][key] for day, by in _by_day(results).items() if arm in by}
            ranked = sorted(base.values())
            context = ", ".join(
                f"{day} has BL-0 A5 {base[day]:.0f}, number {ranked.index(base[day]) + 1} of {len(ranked)} "
                "from the lowest"
                for day, v in d.items()
                if v > 0
            )
            text += f" (A low rank means little delay to remove: {context}.)"
        out.append(text)
    pair = day_deltas(results, "BL-2", key, "BL-1")
    if pair:
        s = spread(pair, key)
        verdict = (
            "BL-2 does not beat the greedy loop on A5 in this sample"
            if s["mean"] >= 0
            else "BL-2 beats the greedy loop on A5 on average"
        )
        out.append(
            f"**BL-2 against BL-1:** A5 is lower with BL-2 on {s['improved']} of {s['n']} days "
            f"(mean {s['mean']:+.1f} train-min, range {s['min']:+.1f} to {s['max']:+.1f}), so {verdict}."
        )
    stops = [
        arm
        for arm in LOOP_ARMS
        if (sp := spread(day_deltas(results, arm, "F1_unplanned_stops"), "F1_unplanned_stops"))["n"]
        and sp["improved"] == sp["n"]
    ]
    if stops:
        out.append(f"Unplanned stops fall on every day with {' and '.join(stops)}.")
    share = {
        arm: sum(r["plans_verified"]["verified"] for r in results if r["arm"] == arm)
        / max(1, sum(r["plans_verified"]["built"] for r in results if r["arm"] == arm))
        for arm in LOOP_ARMS
        if any(r["arm"] == arm for r in results)
    }
    out.append(
        "Share of candidate plans that passed G0-G3: "
        + ", ".join(f"{arm} {100 * v:.0f} %" for arm, v in share.items())
        + ". The loop releases only verified plans, so a low share means the gates rejected most of the "
        "candidates of that arm."
    )
    return out


def markdown(results: Sequence[dict[str, Any]], command: str = REPORT_COMMAND) -> str:
    days = _by_day(results)
    arms = [a for a in LOOP_ARMS if any(a in v for v in days.values())]
    window = results[0]["window_h"] if results else [FROM_H, FROM_H + SPAN_H]
    out = [
        "# Multi-day replay: BL-1 and BL-2 against BL-0 on real days (FV-06, issue #169)",
        "",
        f"Generated by `{command}` from the runs in `docs/kpi/multi-day/`. For each of {len(days)} real days "
        f"({', '.join(days)}) the decision loop runs over the window {window[0]:g}-{window[1]:g} h with the "
        "greedy planner (**BL-1**, controller practice) and with the CP-SAT rolling horizon (**BL-2**, "
        "the DSS), both with auto-accept. The day is then replayed in the twin under the orders each arm "
        "issued and compared with **BL-0** (no decision support) on the same day, demand (the real freight "
        "day) and seed. KPIs are the day's (FR-KPI-05); D1, D2, H1 and H2 are measured in the loop window. "
        "A difference is the arm minus BL-0 (or BL-2 minus BL-1); lower is better for A5, delays, stops, "
        "detention and alerts, higher for punctuality and the conflict KPIs.",
        "",
        "## The runs",
        "",
        "| Day | Arm | Plans (verified G0-G3 / built) | Accepted | Orders | Head-on | Wall time |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        pv = r["plans_verified"]
        out.append(
            f"| {r['day']} | {r['arm']} | {r['plans']} ({pv['verified']} / {pv['built']}) | "
            f"{r['recommendations_accepted']} | {r['orders']} | {r['head_on']} | "
            f"{r['wall_s'] / SECONDS_PER_MIN:.0f} min |"
        )
    shown = [k for k in SHOWN if any(k in r["kpis"] for r in results)]
    out += ["", "## KPIs per day", ""]
    both = len(arms) == len(LOOP_ARMS)  # BL-1 and BL-2 present: BL-2 against BL-1 too
    head = ["Day", "KPI", "BL-0", *arms, *(f"{a} - BL-0" for a in arms), *(["BL-2 - BL-1"] if both else [])]
    out += [
        "| " + " | ".join(head) + " |",
        "|" + "---|" * LABEL_COLUMNS + "---:|" * (len(head) - LABEL_COLUMNS),
    ]
    for day, by in days.items():
        for k in shown:
            base = next(iter(by.values()))["bl0"].get(k)
            vals = [by[a]["kpis"].get(k) if a in by else None for a in arms]
            cells = [_cell(base), *(_cell(v) for v in vals), *(_sign(_delta(v, base)) for v in vals)]
            if both:
                cells.append(_sign(_delta(vals[1], vals[0])))
            out.append(f"| {day} | {k} | " + " | ".join(cells) + " |")
    out += [
        "",
        "## Spread over the days",
        "",
        "Mean and standard deviation of the difference over the days that have both values, its range, "
        "and on how many of those days the KPI improves (worsens) against the reference.",
        "",
        "| KPI | Comparison | Days | Mean | Std | Min | Max | Improves | Worsens |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for k in shown:
        pairs = [(a, "BL-0", f"{a} - BL-0") for a in arms]
        pairs += [("BL-2", "BL-1", "BL-2 - BL-1")] if both else []
        for a, base, name in pairs:
            s = spread(day_deltas(results, a, k, base), k)
            if s["n"] == 0:
                continue
            out.append(
                f"| {k} | {name} | {s['n']} | {_sign(s['mean'])} | {_cell(s['std'])} | {_sign(s['min'])} | "
                f"{_sign(s['max'])} | {s['improved']} | {s['worsened']} |"
            )
    out += ["", "## Reading the results", "", *(f"- {line}" for line in findings(results))]
    return "\n".join(out) + "\n"


def export(results: Sequence[dict[str, Any]], world: run.World, *, generated_by: str) -> dict[str, Any]:
    """An evaluation export (``docs/kpi/evaluation.schema.json``) of the runs, for the management view."""
    rows: list[dict[str, Any]] = []
    window = results[0]["window_h"] if results else [FROM_H, FROM_H + SPAN_H]
    for day, by in _by_day(results).items():
        sc = day_scenario(day)
        title = f"Real day {day}, loop window {window[0]:g}-{window[1]:g} h"
        base = next(iter(by.values()))["bl0"]
        rows.append(
            {
                "scenario": label(day),
                "title": title,
                "arm": "BL-0",
                "kpis": base | dict.fromkeys(WINDOW_KPIS),
                "plans_verified": None,
                "notes": NOTES["BL-0"],
            }
        )
        for arm in LOOP_ARMS:
            if arm in by:
                r = by[arm]
                rows.append(
                    {
                        "scenario": label(day),
                        "title": title,
                        "arm": arm,
                        "kpis": r["kpis"],
                        "plans_verified": r["plans_verified"],
                        "notes": f"{NOTES[arm]} {r['recommendations_accepted']} recommendations accepted, "
                        f"{r['orders']} orders; executed {r['head_on']} head-on, {r['deadlocks']} deadlocks.",
                    }
                )
        ref = reference_row(sc)
        if ref is not None:
            rows.append({**ref, "scenario": label(day), "title": title})
    return {
        "schema_version": SCHEMA_VERSION,
        "run": {
            "network_version": world.layout.network_version,
            "register_version": world.register.version,
            "scenario_set": "real-days-multi-day",
            "seed": 0,
            "window": {"from_h": window[0], "to_h": window[1]},
            "generated_by": generated_by,
            "generated_on": dt.date.today().isoformat(),
            "sample": False,
            "notes": f"{len(_by_day(results))} real days, BL-1 and BL-2 over a {window[1] - window[0]:g} h "
            "loop window each (docs/kpi/multi-day-replay.md); the same day, demand and seed for every arm.",
        },
        "rows": rows,
    }


__all__ = ["DAYS", "FROM_H", "LOOP_ARMS", "SPAN_H", "day_scenario", "export", "markdown", "run_one"]
