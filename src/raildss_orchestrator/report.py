"""The closed-loop replay report: the loop's work and the DSS-vs-BL-0 KPI comparison (issue #90)."""

from __future__ import annotations

from typing import Any

from raildss_dss.kpi import comparison_markdown
from raildss_orchestrator.loop import RunSummary

SECONDS_PER_HOUR = 3600
SECONDS_PER_MIN = 60


def conflict_rows(k: dict[str, Any]) -> list[str]:
    """The D1 / D2 table of a run (DSC-05, DSC-04); ``None`` where there is nothing to measure."""

    def f(x: Any, unit: str = "") -> str:
        return "-" if x is None else f"{x:g}{unit}"

    return [
        "| KPI | Value | Target |",
        "|---|---:|---|",
        f"| Conflicts detected | {k['conflicts']} ({k['near']} near-conflicts) | |",
        f"| D1 median conflict lead time | {f(k['D1_median_conflict_lead_min'], ' min')} "
        "| >= 15 min (DSC-05) |",
        f"| D2 resolved before occurrence | {f(k['D2_resolved_before_occurrence_pct'], ' %')} "
        f"({k['resolved']} resolved, {k['occurred']} occurred, {k['open']} still open) | >= 90 % (DSC-04) |",
    ]


def render(
    summary: RunSummary,
    comparison: dict[str, Any],
    date: str,
    *,
    solver: str,
    conflicts: dict[str, Any] | None = None,
) -> str:
    """Markdown of one replay: window, loop statistics, KPI comparison and the biggest changes per train."""
    gates = [c for cy in summary.cycles for c in cy.candidates]
    ok = sum(c.verified for c in gates)
    lines = [
        f"# Closed-loop replay of {date}",
        "",
        f"Window {summary.from_s / SECONDS_PER_HOUR:g}-{summary.to_s / SECONDS_PER_HOUR:g} h, "
        f"options by {solver}, evaluation auto-accept, identical seed and demand under BL-0 and under "
        "the DSS (FR-SCN-05, FR-SCN-08).",
        "",
        "## The loop",
        "",
        "| What | Value |",
        "|---|---|",
        f"| Clock ticks | {summary.ticks} |",
        f"| Plans (cycles) | {len(summary.cycles)} |",
        f"| Longest gap between plans | {summary.max_replan_gap_s / SECONDS_PER_MIN:.0f} min |",
        f"| Candidate plans verified (G0-G3) / built | {ok} / {len(gates)} |",
        f"| Recommendations released | {summary.released} |",
        f"| Accepted (evaluation policy) | {summary.accepted} |",
        f"| Control orders issued | {summary.orders} ({summary.declined} declined) |",
        f"| Most options for one decision | {summary.max_options_per_decision} |",
        f"| Final statuses | {', '.join(f'{k} {v}' for k, v in sorted(summary.statuses.items())) or '-'} |",
        *_planner_rows(summary),
        "",
        "## KPIs: DSS against BL-0",
        "",
        comparison_markdown(comparison).replace("## ", "### ", 1),
    ]
    if conflicts is not None:
        lines += [
            "",
            "## Conflicts: lead time and resolution (D1, D2)",
            "",
            "The DS-5 conflict log over the window against the ground truth of the DSS arm: a conflict "
            "is resolved if it is no longer predicted before its time and no train of it was held "
            "at its resource in the ground truth; still-open conflicts are left out of D2.",
            "",
            *conflict_rows(conflicts),
        ]
    delta = comparison["deltas"].get("BL-0")
    if delta:
        lines += ["### DSS minus BL-0", "", "| KPI | Delta |", "|---|---|"]
        lines += [f"| {k} | {v:+g} |" for k, v in delta["kpis"].items()]
        if delta["most_affected"]:
            lines += [
                "",
                "Most affected trains (exit delay, min): "
                + ", ".join(f"{m['run_id']} {m['delta_exit_delay_min']:+g}" for m in delta["most_affected"]),
            ]
    return "\n".join(lines) + "\n"


def _planner_rows(summary: RunSummary) -> list[str]:
    """CP-SAT rolling horizon (issue #152): which plan carried the rolling state, the CP-SAT plans against
    the greedy hint, kept frozen decisions and the plan churn."""
    cycles = summary.cycles
    primary = [c for cy in cycles for c in cy.candidates if c.solver.get("primary")]
    if not primary:
        return []
    by_cpsat = sum(cy.planner == "cpsat" for cy in cycles)
    no_worse = sum(
        c.solver.get("objective") is not None
        and c.solver.get("hint_objective") is not None
        and c.solver["objective"] <= c.solver["hint_objective"]
        for c in primary
    )
    hinted = sum(c.solver.get("hint_objective") is not None for c in primary)
    churns = [cy.churn for cy in cycles if cy.churn is not None]
    fixed = sum(int(c.solver.get("fixed") or 0) for c in primary)
    outside = sum(int(c.solver.get("fixed_outside_ir") or 0) for c in primary)
    return [
        f"| Rolling plan by CP-SAT / cycles | {by_cpsat} / {len(cycles)} "
        f"(greedy fallback {len(cycles) - by_cpsat}) |",
        f"| CP-SAT plans verified (G0-G3) / solved | {sum(c.verified for c in primary)} / {len(primary)} |",
        f"| CP-SAT delay objective <= greedy hint | {no_worse} / {hinted} |",
        f"| Frozen decisions imposed (outside the IR) | {fixed} ({outside}) |",
        f"| Plan churn per re-plan (mean, max) | "
        f"{(sum(churns) / len(churns)) if churns else 0:.1f}, {max(churns, default=0)} |",
    ]
