"""KPIs of the scenario library, under BL-0 and under the decision loop (issues #57, #155; FR-SCN-04).

* **BL-0** (every executable scenario of library v1): the twin plays the scenario with its injected
  disruptions and its seed, nothing else changes; the KPIs come from the ground-truth events (FR-KPI-01).
* **Decision loop** (scenarios whose disruptions are line blocks, speed restrictions, loops out of use, signal
  failures, loco failures, OHE blocks and trips, crew out of hours and late entries): the loop plans in a
  window around the first disruption, verifies and accepts its options, and the day is compared with BL-0
  under the same disruptions (FR-KPI-05). Line blocks are closures (twin directives); the other incidents are
  applied by the injector to the world and to every plan once announced (at their start; plans assume the
  announced ETR until it passes, issue #156), and late entries are known from the start of the window (issue
  #155). Disruption options (re-cross, short-terminate / cancel, contingent ETR plans) are issue #156.
  Two arms: **BL-1**, the loop with the greedy planner (controller practice), and **DSS**, the loop with the
  CP-SAT rolling horizon (#152).
"""

from __future__ import annotations

import statistics
from typing import Any

from raildss_dss.conflict_kpi import gt_occurrence
from raildss_dss.kpi import KpiReport
from raildss_optimiser.greedy import GreedyDispatcher
from raildss_orchestrator.loop import Disruption, Orchestrator, RunSummary
from raildss_orchestrator.report import alert_rows, conflict_rows
from raildss_orchestrator.twin import TwinDss, TwinPlanner, day_kpis
from raildss_twin import run, scenario
from raildss_twin.kpi_eval import run_arm
from raildss_twin.scenario import Scenario

SECONDS_PER_MIN = 60
SECONDS_PER_HOUR = 3600
LEAD_S = 15 * SECONDS_PER_MIN  # the loop starts this long before the first disruption
SPAN_S = 90 * SECONDS_PER_MIN  # and runs this long
NOTICE_S = 30 * SECONDS_PER_MIN  # a closure is known this long before it starts
TICK_S = 120.0
SHOWN = (
    "A2_section_punctuality_pct",
    "A4_mean_arrival_delay_min",
    "A5_weighted_delay_train_min",
    "A6_mean_exit_delay_min",
    "A6_p90_exit_delay_min",
    "B1_trains_exited",
    "C2_freight_detention_h",
    "F1_unplanned_stops",
)


def bl0(world: run.World, sc: Scenario) -> KpiReport:
    """The scenario under BL-0: its seed, freight day and injected disruptions, no decision support."""
    kw = scenario.run_kwargs(sc)
    return run_arm(
        world,
        sc.wtt_date,
        "BL-0",
        sc.id,
        directives=kw["directives"],
        seed=kw["seed"],
        day_instance=kw["day_instance"],
        setup=kw["setup"],
    )


LOOP_KINDS = {
    "line_block",
    "tsr",
    "point_failure",
    "signal_failure",
    "entry_delay",
    "loco_failure",  # issue #156: SCN-04
    "ohe_block",  # SCN-08
    "ohe_trip",  # SCN-09
    "crew_hours",  # SCN-10
}
PERCENTILES = 20  # statistics.quantiles cut points: 5 % steps
P95 = PERCENTILES - 2  # index of the 95th percentile among the cut points
PRESENTED = "PRESENTED"  # a recommendation nobody decided on
ARMS = {"BL-1": "greedy", "DSS": "cpsat"}  # the arm and its planner (#152)


def loopable(sc: Scenario) -> bool:
    return bool(sc.disruptions) and all(d.type in LOOP_KINDS for d in sc.disruptions)


def with_loop(
    world: run.World, sc: Scenario, arm: str = "BL-1", span_s: float = SPAN_S
) -> tuple[RunSummary, dict[str, Any]]:
    """The decision loop around the scenario's first disruption with the arm's planner, and the day's KPIs
    against BL-0 under the same disruptions."""
    closures = GreedyDispatcher(world).closures_of(sc)
    incidents = [d for d in scenario.injected(sc) if d.kind != "line_block"]  # line blocks are closures
    delays = scenario.entry_delays(sc)
    first = min(d.start_s for d in sc.disruptions)
    from_s, to_s = first - LEAD_S, first - LEAD_S + span_s
    day = sc.wtt_date
    dss = TwinDss(world, day, until_s=to_s, closures=closures, incidents=incidents, entry_delays=delays)
    planner = TwinPlanner(world, day, planner=ARMS[arm], cpsat_enabled=ARMS[arm] == "cpsat", alternatives=0)
    loop = Orchestrator(
        world.register,
        dss,
        planner,
        disruptions=tuple(Disruption(c, c.start_s - NOTICE_S) for c in closures),
        incidents=tuple((d, d.start_s) for d in incidents),  # unplanned: known when they start
        entry_delays=delays,
    )
    summary = loop.run(from_s, to_s, TICK_S)
    holds, lines = loop.commitments()
    gt: list[Any] = []
    comparison = day_kpis(
        world,
        day,
        closures=closures,
        exit_windows={},
        holds=holds,
        lines=lines,
        incidents=incidents,
        entry_delays=delays,
        arm=arm,
        events_out=gt,
        terminations=loop.terminations(),
    )
    if loop.conflict_log is not None:  # D1 and D2: the DS-5 log against the ground truth
        comparison["conflicts"] = loop.conflict_log.kpis(
            gt_occurrence(gt, dss.origin, world.register), to_s=to_s
        ).as_dict()
    comparison["alerts"] = loop.alert_log.kpis(from_s, to_s).as_dict()  # H1, H2 (DSC-09)
    return summary, comparison


Loop = tuple[Scenario, dict[str, tuple[RunSummary, dict[str, Any]]]]


def render(rows: list[tuple[Scenario, KpiReport]], loops: list[Loop], command: str = "") -> str:
    out = [
        "# KPIs of the scenario library (issues #57, #155)",
        "",
        f"Generated by `{command or 'python -m raildss_orchestrator scenarios'}`.",
        "",
        "## BL-0: the twin with the scripted disruptions, no decision support",
        "",
        "| Scenario | Disruptions | "
        + " | ".join(k.split("_", 1)[0] + " " + k.split("_", 1)[1] for k in SHOWN)
        + " |",
        "|---|---|" + "---:|" * len(SHOWN),
    ]
    for sc, rep in rows:
        kinds = ", ".join(sorted({d.type for d in sc.disruptions})) or "none"
        out.append(f"| {sc.id} | {kinds} | " + " | ".join(f"{rep.kpis[k]:g}" for k in SHOWN) + " |")
    out += [
        "",
        "## Decision loop on the disruption scenarios",
        "",
        "The loop plans in a window around the first disruption with options verified by gates G0-G3 and "
        "accepted by the evaluation policy. **BL-1** is the loop with the greedy planner (controller "
        "practice), **DSS** the loop with the CP-SAT rolling horizon. Every arm of the day runs under the "
        "same disruptions (line blocks as closures, other incidents through the injector, late entries), so "
        "the BL-0 column here can differ from the BL-0 table above.",
    ]
    for sc, arms in loops:
        out += ["", f"### {sc.id}: {sc.title}", ""]
        for arm, (summary, _) in arms.items():
            verified = sum(c.verified for cy in summary.cycles for c in cy.candidates)
            built = sum(len(cy.candidates) for cy in summary.cycles)
            out.append(
                f"- {arm}: window {summary.from_s / SECONDS_PER_HOUR:.2f}-"
                f"{summary.to_s / SECONDS_PER_HOUR:.2f} h,"
                f" {len(summary.cycles)} plans, **{verified} of {built} candidate plans passed G0-G3**, "
                f"{summary.accepted} recommendations accepted, {summary.orders} orders."
            )
            out += [f"  - {line}" for line in option_lines(summary)]
        names = list(arms)
        base = next(iter(arms.values()))[1]["arms"]["BL-0"]
        both = {"DSS", "BL-1"} <= set(names)
        head = [*names, "BL-0", *(f"{a} - BL-0" for a in names), *(["DSS - BL-1"] if both else [])]
        out += ["", "| KPI | " + " | ".join(head) + " |", "|---|" + "---:|" * len(head)]
        for k in SHOWN:
            vals = {a: comp["arms"][a][k] for a, (_, comp) in arms.items()}
            row = (
                [f"{vals[a]:g}" for a in names]
                + [f"{base[k]:g}"]
                + [f"{vals[a] - base[k]:+.2f}" for a in names]
            )
            if both:
                row.append(f"{vals['DSS'] - vals['BL-1']:+.2f}")
            out.append(f"| {k} | " + " | ".join(row) + " |")
        for arm, (_, comp) in arms.items():
            if "conflicts" in comp:
                out += ["", f"Conflicts (D1, D2), {arm}:", "", *conflict_rows(comp["conflicts"])]
            if "alerts" in comp:
                out += ["", f"Alert load (H1, H2), {arm}:", "", *alert_rows(comp["alerts"])]
    return "\n".join(out) + "\n"


def option_lines(summary: RunSummary) -> list[str]:
    """Disruption options of a loop run (issue #156): options built and verified by kind, those accepted,
    contingent plans and their switch rules, and the disruption first-plan latency (DSC-06)."""
    kinds = ("recross", "short-term", "cancel")
    out = []
    for k in kinds:
        built = [c for cy in summary.cycles for c in cy.candidates if c.name == k]
        taken = [o for cy in summary.cycles for o in cy.released if o.candidate == k]
        taken = [o for o in taken if o.status != PRESENTED]
        if built:
            out.append(
                f"option {k}: {sum(c.verified for c in built)} of {len(built)} verified, "
                f"{len(taken)} recommendations decided"
            )
    rules = [c for cy in summary.cycles for c in cy.contingent]
    if rules:
        out.append(
            f"contingent ETR plans: {sum(c.verified for c in rules)} of {len(rules)} verified; "
            + "; ".join(sorted({c.rule for c in rules}))
        )
    first = sorted(
        s for cy in summary.cycles if "disruption" in cy.triggers and (s := cy.first_plan_s) is not None
    )
    if first:
        cuts = statistics.quantiles(first, n=PERCENTILES, method="inclusive") if len(first) > 1 else []
        p95 = cuts[P95] if cuts else first[0]
        out.append(
            f"disruption first plan: P95 {p95:.1f} s over {len(first)} disruption cycles "
            f"(max {first[-1]:.1f} s; DSC-06 budget 60 s)"
        )
    return out


def run_all(
    world: run.World,
    ids: list[str],
    *,
    with_loops: bool,
    arms: tuple[str, ...] = tuple(ARMS),
    span_s: float | None = None,
) -> tuple[list[tuple[Scenario, KpiReport]], list[Loop]]:
    library = [s for s in scenario.load_library_v1() if s.executable and (not ids or s.id in ids)]
    rows = [(s, bl0(world, s)) for s in library]
    loops: list[Loop] = []
    if with_loops:
        for s in library:
            if loopable(s):
                loops.append((s, {arm: with_loop(world, s, arm, span_s or SPAN_S) for arm in arms}))
    return rows, loops
