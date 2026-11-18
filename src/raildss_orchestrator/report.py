"""The closed-loop replay report: the loop's work and the DSS-vs-BL-0 KPI comparison (issue #90)."""

from __future__ import annotations

from typing import Any

from raildss_dss.kpi import comparison_markdown
from raildss_orchestrator.loop import RunSummary

SECONDS_PER_HOUR = 3600
SECONDS_PER_MIN = 60


def render(summary: RunSummary, comparison: dict[str, Any], date: str, *, solver: str) -> str:
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
        "",
        "## KPIs: DSS against BL-0",
        "",
        comparison_markdown(comparison).replace("## ", "### ", 1),
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
