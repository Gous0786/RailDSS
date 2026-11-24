"""Disruption and what-if export for the web UI (spec FR-UI-11; issue #172).

``build`` turns a disruption scenario into one chart document: the day under BL-0 with the scenario's
disruptions (the baseline lines, stations, alerts, mimic of the chart export), the scenario's **disruptions**
(type, cause, location, start, ETR and its source, lines affected, linked trains) and the **what-if
variants**: the same day replayed under what each arm of the decision loop ordered (BL-1: the greedy loop;
DSS: the CP-SAT rolling horizon), one option per arm in ``preview.options`` with the lines of the trains it
changes, the impact per train, the rule auditor over the replay and the KPIs of the arm against the
baseline. The web app draws an option on the chart against the baseline; ``whatif`` carries the plan
comparison table. The mimic shows the baseline run only.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from raildss_orchestrator.loop import CycleRecord, RunSummary
from raildss_orchestrator.scenarios import ARMS, SPAN_S, with_loop
from raildss_twin import chart, run, scenario
from raildss_twin.scenario import Scenario, ScenarioDisruption

SECONDS_PER_MIN = 60
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 24 * SECONDS_PER_HOUR
MAX_ORDERS = 8  # orders listed per variant (the total is given too)
ARM_TITLES = {
    "BL-1": "BL-1: the loop with the greedy planner",
    "DSS": "DSS: the loop with the CP-SAT rolling horizon",
}


def _block_km(world: run.World, block: str | None, through: str | None) -> tuple[float, float] | None:
    """The km range of a block section, or of the run from ``block`` to ``through``."""
    if block is None or block not in world.layout.blocks:
        return None
    ends = [world.layout.blocks[block]]
    if through is not None and through in world.layout.blocks:
        ends.append(world.layout.blocks[through])
    kms = [world.layout.km(op) for b in ends for op in (b.from_op, b.to_op)]
    return round(min(kms), chart.KM_DIGITS), round(max(kms), chart.KM_DIGITS)


def disruption_records(world: run.World, sc: Scenario) -> list[dict[str, Any]]:
    """The scenario's disruptions as the incident list of the view (FR-UI-11)."""
    exits = scenario.exit_windows(sc)
    out: list[dict[str, Any]] = []
    for d in sc.disruptions:
        realized = d.realize(sc.seed, sc.wtt_date)
        if realized is not None:
            duration_s = realized.duration_s
            etr_s = realized.etr_s
        elif d.type == "entry_delay":
            duration_s, etr_s = d.entry_delay_s(sc.seed), None
        elif d.type == "exit_blocked":
            station = scenario.load_boundaries().boundaries[str(d.boundary)].station
            start, end = next(
                (w for w in exits.get(station, []) if w[0] == d.start_s), (d.start_s, d.start_s)
            )
            duration_s, etr_s = end - start, None
        else:  # special_train: an insertion, no duration
            duration_s, etr_s = 0.0, None
        km = _block_km(world, d.block_id, d.through_block)
        out.append(
            {
                "id": d.incident_id,
                "type": d.type,
                "cause_code": d.train_no,
                "location": _location(d),
                "line": d.line_id,
                "km_from": km[0] if km else None,
                "km_to": km[1] if km else None,
                "start_s": d.start_s,
                "end_s": d.start_s + duration_s,
                "etr_s": etr_s,
                "etr_sigma_min": d.etr.sigma_min if d.etr else None,
                "etr_source": "SM_ESTIMATE" if etr_s is not None else None,
                "lines_affected": _lines_affected(d),
                "linked_trains": [f"{d.train_no}@{sc.wtt_date.isoformat()}"] if d.train_no else [],
                "single_line_working": d.single_line_working,
                "status": "scripted",
                "owner": "section master (scripted scenario)",
                "note": d.note,
            }
        )
    return sorted(out, key=lambda r: (r["start_s"], r["id"]))


def _location(d: ScenarioDisruption) -> str:
    where = d.block_id or d.station or d.boundary or d.train_no or ""
    if d.through_block:
        where = f"{where} to {d.through_block}"
    if d.type == "point_failure":
        where = f"{d.station} line {d.line_no}"
    return where


def _lines_affected(d: ScenarioDisruption) -> list[str]:
    if d.block_id is not None:
        return [f"{d.block_id}/{d.line_id or 'all lines'}"]
    if d.type == "point_failure":
        return [f"{d.station}/line {d.line_no}"]
    return []


def contrast_fields(cycle: CycleRecord) -> dict[str, Any]:
    """The contrast of one planning cycle for the card (FR-UI-09, issue #174): the most contested decision it
    released (the one with the most precedences, then options) and the 'why not' re-solves of the cycle.
    Both are optional in the export: a cycle without a decision, or with one precedence and no re-solve, gives
    ``decision`` None and an empty ``why_not``, and the card says so."""
    contested = max(cycle.decisions, key=lambda d: (d.precedences, d.options), default=None)
    # a re-solve belongs to a decision by its trains (two decisions can share a resource, issue #196); one
    # without trains (an older record) is kept
    mine = [
        c
        for c in cycle.contrasts
        if contested is not None and (not c.trains or set(c.trains) == set(contested.trains))
    ]
    return {
        "decision": None if contested is None else contested.model_dump(),
        "why_not": [
            {
                "first": c.first,
                "resource": c.resource,
                "verified": c.verified,
                "reversed": c.reversed,
                "cost_min": c.cost_min,
                "fail": list(c.fail),
                "answer": c.answer,
            }
            for c in mine
        ],
    }


def _orders(summary: RunSummary) -> list[str]:
    """The orders of the best released option of each planning cycle, first occurrence first."""
    seen: dict[str, None] = {}
    for cycle in summary.cycles:
        for action in cycle.released[0].actions if cycle.released else []:
            seen.setdefault(action, None)
    return list(seen)


def variant(
    world: run.World,
    sc: Scenario,
    arm: str,
    summary: RunSummary,
    comparison: dict[str, Any],
    baseline: dict[str, Any],
    base_sim: Any,
    arm_sim: Any,
) -> dict[str, Any]:
    """One what-if option: the day under the orders of an arm, against the baseline run."""
    day = sc.wtt_date
    plans = [t.plan for t in base_sim.trains]
    weights = {k: float(v) for k, v in world.register.value("policy.weights").items()}
    actual = chart.lines_from_events(base_sim.events, plans, day)
    after = chart.lines_from_events(arm_sim.events, plans, day)
    proposed = {rid: pts for rid, pts in after.items() if pts != actual.get(rid)}
    impacts = chart._impacts(plans, actual, proposed, weights)
    orders = _orders(summary)
    verified = sum(c.verified for cy in summary.cycles for c in cy.candidates)
    built = sum(len(cy.candidates) for cy in summary.cycles)
    return {
        "id": arm,
        "title": ARM_TITLES.get(arm, arm),
        "arm": arm,
        "orders": orders[:MAX_ORDERS],
        "orders_total": len(orders),
        "trains": proposed,
        "impacts": impacts,
        "weighted_delay_delta_min": round(
            sum(i["weight"] * i["delta_s"] for i in impacts) / SECONDS_PER_MIN, 1
        ),
        "kpis": {"baseline": baseline, "variant": comparison["arms"][arm]},
        "verification": chart._verification(world, arm_sim, day),
        "loop": {
            "window_h": [
                round(summary.from_s / SECONDS_PER_HOUR, 2),
                round(summary.to_s / SECONDS_PER_HOUR, 2),
            ],
            "plans": len(summary.cycles),
            "candidates_verified": f"{verified} of {built}",
            "recommendations_accepted": summary.accepted,
            "orders_issued": summary.orders,
        },
        "explanation": {
            "why": (
                f"{ARM_TITLES.get(arm, arm)}: {summary.accepted} recommendations accepted and "
                f"{summary.orders} orders issued in the window; the twin replays the whole day under them. "
                f"{len(impacts)} trains change against the baseline."
            ),
            "why_not": "The baseline is BL-0: the same day and disruptions with no decision support.",
            "weights": weights,
            "hard_rules": [
                {"rule": r, "applied": False, "note": "checked by the verifier gates G0-G3 of the loop"}
                for r in world.register.value("policy.hard_rules")
            ],
        },
        "confidence": None,
        "assumptions": [
            "Twin ground truth: no prediction uncertainty is modelled in the replay.",
            "The orders are those the loop's evaluation policy accepted automatically.",
        ],
    }


def build(
    world: run.World, sc: Scenario, arms: tuple[str, ...] = tuple(ARMS), span_s: float = SPAN_S
) -> dict[str, Any]:
    """The chart document of a disruption scenario with its disruptions and what-if variants."""
    day = sc.wtt_date
    ran: dict[str, tuple[RunSummary, dict[str, Any], dict[str, Any]]] = {}
    for arm in arms:
        sims: dict[str, Any] = {}
        summary, comparison = with_loop(world, sc, arm, span_s, sims_out=sims)
        ran[arm] = (summary, comparison, sims)
    first = ran[arms[0]]
    base_sim = first[2]["BL-0"]
    baseline = first[1]["arms"]["BL-0"]
    plans = [t.plan for t in base_sim.trains]
    report = SimpleNamespace(
        scenario_id=f"{sc.id}-bl0",
        trace_hash=base_sim.hash.hexdigest(),
        completed=not any(t.status == "stuck" for t in base_sim.trains),
    )
    options = [
        variant(world, sc, arm, summary, comparison, baseline, base_sim, sims[arm])
        for arm, (summary, comparison, sims) in ran.items()
    ]
    doc = chart.document(world, day, plans, base_sim, report)
    return {
        **doc,
        "scenario": {
            "id": sc.id,
            "title": sc.title,
            "family": sc.family,
            "seed": sc.seed,
            "profile": sc.degradation_profile,
            "span_h": round(span_s / SECONDS_PER_HOUR, 2),
        },
        "disruptions": disruption_records(world, sc),
        "whatif": {"baseline": {"arm": "BL-0", "kpis": baseline}, "arms": [a for a in ran]},
        "preview": {
            "id": f"WHATIF-{sc.id}",
            "demo": False,
            "context": f"{sc.id}: {sc.title}",
            "options": options,
            "valid_until_s": SECONDS_PER_DAY,
        },
    }


def merge_variants(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """``new`` with the variants of ``old`` that it does not have: the arms of a scenario can be exported one
    at a time (each takes a loop run) into the same file. Both must be the baseline of the same scenario."""
    if (old["scenario"], old["trace_hash"]) != (new["scenario"], new["trace_hash"]):
        return new
    have = {o["id"] for o in new["preview"]["options"]}
    options = [*new["preview"]["options"], *(o for o in old["preview"]["options"] if o["id"] not in have)]
    order = {arm: i for i, arm in enumerate(ARMS)}
    options.sort(key=lambda o: order.get(o["arm"], len(order)))
    return {
        **new,
        "preview": {**new["preview"], "options": options},
        "whatif": {**new["whatif"], "arms": [o["arm"] for o in options]},
    }


def write(doc: dict[str, Any], out_dir: Path) -> Path:
    """Write the document as ``<scenario id>.json`` (keeping the variants of other arms already exported for
    the same baseline) and list it in ``index.json`` next to the days."""
    out_dir.mkdir(parents=True, exist_ok=True)
    nl = "\n"
    name = str(doc["scenario"]["id"])
    path = out_dir / f"{name}.json"
    if path.exists():
        doc = merge_variants(json.loads(path.read_text(encoding="utf-8")), doc)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, separators=(",", ":")) + nl, encoding="utf-8", newline=nl)
    tmp.replace(path)
    index = out_dir / "index.json"
    days = set(json.loads(index.read_text(encoding="utf-8"))["days"]) if index.exists() else set()
    index.write_text(json.dumps({"days": sorted(days | {name})}) + nl, encoding="utf-8", newline=nl)
    return path
