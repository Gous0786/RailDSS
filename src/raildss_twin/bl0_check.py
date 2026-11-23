"""BL-0 against the historical day: is it a fair do-nothing reference? (issue #93, DR-0002 F15)

BL-0 is the twin replaying a day with no control: passenger delays come from trains queueing first come
first served. The only historical
actuals in the dataset are freight (the K1 reference, ``docs/kpi/historical-reference-ktv-psa.md``,
says ``passenger_delay`` is not available), so the check compares what can be compared: the freight
section times of the same loads, and the passenger delay tail BL-0 produces. The note is generated, and
so are its sentences about the figures (:func:`delay_shape`, :func:`freight_comparison`): they follow the
table for any day instead of repeating what one day showed (issue #149).
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from pathlib import Path
from typing import Any

from raildss_dss.kpi import KpiReport
from raildss_schema.entities import FreightLoad
from raildss_twin import kpi_eval, run
from raildss_twin.sim import token

SECONDS_PER_HOUR = 3600.0
SECONDS_PER_MINUTE = 60.0
LONG_DELAY_MIN = 60.0
P90 = 0.9
TAIL_SHARE = 0.5  # a median below this share of the mean: a few long delays pull the mean up
SIMILAR_LOW, SIMILAR_HIGH = 0.8, 1.25  # history over BL-0 within these: about the same
REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "docs" / "kpi"
REFERENCE_JSON = OUT_DIR / "historical-reference-ktv-psa.json"


def note_name(day: dt.date) -> str:
    return f"bl0-reference-check-{day.isoformat()}.md"


def _q(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(q * len(s)))]


def freight_pairs(world: run.World, report: KpiReport) -> list[tuple[float, float, bool]]:
    """Per freight load that left the section: (historical hours, BL-0 hours, stabled in the log)."""
    loads = {token(x.load_id): x for x in world.tables["freight_load"] if isinstance(x, FreightLoad)}
    out = []
    for r in report.trains:
        if r.kind != "freight" or r.entry_s is None or r.exit_s is None or r.run_id not in loads:
            continue
        ld = loads[r.run_id]
        if ld.entry_time is None or ld.exit_time is None:
            continue
        hist = (ld.exit_time - ld.entry_time).total_seconds() / SECONDS_PER_HOUR
        out.append((hist, (r.exit_s - r.entry_s) / SECONDS_PER_HOUR, bool(ld.stabled)))
    return out


def summarise(world: run.World, report: KpiReport, reference: dict[str, Any], day: dt.date) -> dict[str, Any]:
    d = day.isoformat()
    pairs = freight_pairs(world, report)
    running = [p for p in pairs if not p[2]]
    pax = [r.exit_delay_min for r in report.trains if r.kind == "passenger" and r.exit_delay_min is not None]
    hist_day = reference["delay_indicators"]["by_day"][d]
    return {
        "day": d,
        "kpis": report.kpis,
        "pax_n": len(pax),
        "pax_mean": statistics.fmean(pax),
        "pax_median": statistics.median(pax),
        "pax_max": max(pax),
        "pax_over_long": sum(1 for x in pax if x > LONG_DELAY_MIN),
        "frt_n": len(pairs),
        "frt_stabled": len(pairs) - len(running),
        "frt_hist_median": statistics.median(p[0] for p in running),
        "frt_hist_p90": _q([p[0] for p in running], P90),
        "frt_bl0_median": statistics.median(p[1] for p in running),
        "frt_bl0_p90": _q([p[1] for p in running], P90),
        "frt_hist_mean": statistics.fmean(p[0] for p in running),
        "frt_bl0_mean": statistics.fmean(p[1] for p in running),
        "hist_detention_h": hist_day["detention_hours"],
        "hist_running_excess_h": hist_day["running_excess_hours"],
    }


def delay_shape(s: dict[str, Any]) -> str:
    """The sentence about the passenger delay distribution, from the figures of the table."""
    longest_h = s["kpis"]["A4_max_arrival_delay_min"] / SECONDS_PER_HOUR * SECONDS_PER_MINUTE
    head = f"The median exit delay is {s['pax_median']:.1f} min against a mean of {s['pax_mean']:.1f} min."
    if s["pax_over_long"] == 0:
        return f"{head} No train is more than {LONG_DELAY_MIN:.0f} min late."
    tail = (
        f"{s['pax_over_long']} trains are more than {LONG_DELAY_MIN:.0f} min late, the longest arrival delay "
        f"at a station {longest_h:.1f} h."
    )
    if s["pax_median"] < TAIL_SHARE * s["pax_mean"]:
        return f"{head} Most trains are close to on time and a tail of long delays pulls the mean up: {tail}"
    return f"{head} The delay is spread over many trains rather than a short tail: {tail}"


def freight_comparison(s: dict[str, Any]) -> tuple[str, str]:
    """(the headline, the sentence) of the freight comparison: faster, slower or about the same."""
    ratio = s["frt_hist_median"] / s["frt_bl0_median"] if s["frt_bl0_median"] else float("inf")
    if ratio > SIMILAR_HIGH:
        return "Freight is faster in BL-0 than in the log.", (
            f"History is {ratio:.1f} times slower than BL-0 (median section time) for the same loads."
        )
    if ratio < SIMILAR_LOW:
        return "Freight is slower in BL-0 than in the log.", (
            f"BL-0 is {1 / ratio:.1f} times slower than history (median section time) for the same loads."
        )
    return "Freight takes about as long in BL-0 as in the log.", (
        f"The median section times are {s['frt_hist_median']:.2f} h (history) and "
        f"{s['frt_bl0_median']:.2f} h (BL-0) for the same loads."
    )


def render(s: dict[str, Any]) -> str:
    k = s["kpis"]
    headline, comparison = freight_comparison(s)
    return f"""# BL-0 against the historical day {s["day"]} (K1 reference check, issue #93, DR-0002 F15)

Generated by `python -m raildss_twin bl0-check --write`. BL-0 is the twin replaying the day with no control
(first come first served). No timestamps.

## What can be compared

The historical reference (`historical-reference-ktv-psa.md`) is built from the freight movement log. The
dataset has **no passenger actuals**, so BL-0 passenger delay cannot be checked against history. Freight can.

## BL-0 passenger delay ({s["pax_n"]} trains that left the section)

| Measure | BL-0 |
|---|---|
| Mean exit delay (min) | {s["pax_mean"]:.1f} |
| Median exit delay (min) | {s["pax_median"]:.1f} |
| Longest exit delay (min) | {s["pax_max"]:.1f} |
| Trains more than {LONG_DELAY_MIN:.0f} min late | {s["pax_over_long"]} |
| Longest arrival delay at any station (min) | {k["A4_max_arrival_delay_min"]} |
| Section punctuality (%) | {k["A2_section_punctuality_pct"]} |

{delay_shape(s)}

## Freight: the same loads, history against BL-0

{s["frt_n"]} loads of the day left the section in BL-0; {s["frt_stabled"]} of them were stabled in the log
(a stop over 6 h) and are left out below.

| Section time per load (h) | History | BL-0 |
|---|---|---|
| Median | {s["frt_hist_median"]:.2f} | {s["frt_bl0_median"]:.2f} |
| Mean | {s["frt_hist_mean"]:.2f} | {s["frt_bl0_mean"]:.2f} |
| p90 | {s["frt_hist_p90"]:.2f} | {s["frt_bl0_p90"]:.2f} |

History, whole day: detention {s["hist_detention_h"]} h (dwell above 30 min) and running time above the
block p10 {s["hist_running_excess_h"]} h. BL-0 freight standing: {k["C2_freight_detention_h"]} h.

## Reading the gap

1. **{headline}** The twin runs a freight train from its logged entry at the calibrated running time. It
   adds no crew changes, no waits for a path and no long halts, and the log has them. {comparison}
2. **So BL-0 is not a reproduction of the real day.** Its passenger delay comes from its own
   first-come-first-served rule, with no controller decisions and none of the real freight halts. The
   dataset has no passenger log, so how far its passenger delay is from what passengers experienced is
   unknown; this check cannot measure it.
3. **The delays are a property of the rule, not a measured fact.** Median {s["pax_median"]:.1f} min, mean
   {s["pax_mean"]:.1f} min and {s["pax_over_long"]} trains over {LONG_DELAY_MIN:.0f} min late are what the
   do-nothing rule gives; the DSS has to improve on them, which is the use BL-0 is accepted for.

## Decision

BL-0 is **accepted as the do-nothing reference for comparing the DSS with BL-0 under identical demand**
(FR-SCN-05, FR-SCN-08). It is **not** an estimate of the delay the day really had, so no KPI may be
reported as if BL-0 were calibrated to history. Reports quote the BL-0 and DSS columns together
(`replay-ktv-psa-2025-08-20.md`), never BL-0 alone as "the actual delay". If passenger actuals become
available, replace the passenger part of this check with a per-train and per-station delay comparison.
"""


def run_check(day: dt.date, world: run.World | None = None) -> str:
    world = world or run.load_world()
    report = kpi_eval.run_arm(world, day, "BL-0", f"day {day.isoformat()}")
    reference = json.loads(REFERENCE_JSON.read_text(encoding="utf-8"))
    return render(summarise(world, report, reference, day))


def write(day: dt.date, text: str, out_dir: Path = OUT_DIR) -> Path:
    path = out_dir / note_name(day)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path
