"""Control-chart data for the web UI (spec FR-UI-02, FR-UI-03; issue #86).

``chart_data`` replays a real day in the twin and returns what the time-distance chart draws:

* **stations** of the published network (block stations and halts) with their chainage and loops;
* per train, the **actual** line (the twin's ground-truth position every state period) and, for
  timetabled trains, the **booked** WTT line;
* a **demo recommendation preview**: one option ("hold a freight train at its entry for
  ``ui.preview_demo_hold_min``") replayed in the twin, with the lines of every train it changes. The
  do-nothing baseline is the actual run. It is labelled DEMO until the optimiser produces real
  recommendations. The card (issue #88, FR-UI-09) adds the impact per train against the baseline
  (the most affected first), the verifier audit of the option's replay, the policy weights and hard
  rules (OD-04), the assumptions and the expiry;
* **per-train facts** for the queue and the KPI strip (FR-UI-07, FR-UI-10): class and policy weight,
  freight load, entry and exit delay of timetabled trains;
* the **schematic mimic** (FR-UI-06, :mod:`raildss_twin.mimic`): the published layout and the run's occupancy;
* **alerts** (FR-UI-08) the twin observed: a timetabled train late beyond the amber delay badge, and a
  train held for a resource beyond the yellow one. They are ground truth, not DS-5 output, and say so.

Times are seconds since the start of the day; distances are km from KTV.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from raildss_common.envelope import EventType
from raildss_twin import run
from raildss_twin.mimic import mimic_data
from raildss_twin.plans import Plan, day_start
from raildss_twin.sim import Sim
from raildss_verifier import network
from raildss_verifier.auditor import TrainMeta, audit

FORMAT = "raildss-chart"
VERSION = 3  # 3: the schematic mimic (issue #87)
SECONDS_PER_MIN = 60
KM_DIGITS = 3  # rounding of chainage in the export (metres)


def _lines(sim: Sim, day: dt.date) -> dict[str, list[list[float]]]:
    """``run id -> [[t, km], ...]`` from the ground-truth train states (one per state period)."""
    return lines_from_events(sim.events, [t.plan for t in sim.trains], day)


def lines_from_events(events: Any, plans: list[Plan], day: dt.date) -> dict[str, list[list[float]]]:
    """The same from the events of a run and the plans it ran."""
    origin = day_start(day)
    names = {p.run_id.replace(" ", "_"): p.run_id for p in plans}
    out: dict[str, list[list[float]]] = {}
    for ev in events:
        if ev.event_type is not EventType.TRAIN_STATE or ev.location is None or ev.train_run_id is None:
            continue
        km = ev.location.km
        if km is None:
            continue
        t = round((ev.event_time - origin).total_seconds())
        out.setdefault(names[ev.train_run_id], []).append([t, round(km, KM_DIGITS)])
    return out


def _booked(world: run.World, plan: Plan) -> list[list[float]]:
    if not plan.timetabled:
        return []
    pts: list[list[float]] = []
    for n in plan.nodes:
        km = round(world.layout.km(n.op), KM_DIGITS)
        pts.append([round(n.arr), km])
        if n.dep != n.arr:
            pts.append([round(n.dep), km])
    return pts


def _stations(world: run.World) -> list[dict[str, Any]]:
    out = []
    for code in world.layout.order:
        op = world.layout.ops[code]
        lines = [s for s in world.layout.station_lines.get(code, []) if s.status == "in_use"]
        loops = [s for s in lines if s.kind == "loop"]
        out.append(
            {
                "code": code,
                "name": op.name,
                "km": round(op.km, KM_DIGITS),
                "halt": op.is_halt,
                "crossing": op.crossing_facility,
                "lines": len(lines),
                "loops": len(loops),
                "max_loop_m": max((s.csr_m for s in loops if s.csr_m is not None), default=None),
            }
        )
    return out


WAITING = "waiting"
FREIGHT_CLASS = "freight_other"  # the log gives no timetable for freight: the lowest weight (OD-04)


def _class_of(plan: Plan) -> str:
    return FREIGHT_CLASS if plan.kind == "freight" else plan.category


def _load(plan: Plan) -> dict[str, float] | None:
    if plan.kind != "freight":
        return None
    c = plan.template.consist
    return {"wagons": sum(v.count for v in c.vehicles), "tonnes": round(c.trailing_t or 0.0)}


def _delays(booked: list[list[float]], actual: list[list[float]]) -> tuple[float | None, float | None]:
    """Entry and exit delay (s) of a timetabled train: its first and last point against the booking."""
    if not booked or not actual:
        return None, None
    return actual[0][0] - booked[0][0], actual[-1][0] - booked[-1][0]


def _alert(
    kind: str, priority: str, train: str, location: str, km: float, t: float, detail: str
) -> dict[str, Any]:
    return {
        "id": "",
        "priority": priority,
        "type": kind,
        "trains": [train],
        "location": location,
        "km": round(km, KM_DIGITS),
        "time_s": round(t),
        "lead_min": 0,
        "recommendation": None,
        "state": "open",
        "raised_by": "twin ground truth (DS-5 is not wired to the chart export)",
        "detail": detail,
    }


def _alerts(sim: Sim, day: dt.date, plans: list[Plan], world: run.World) -> list[dict[str, Any]]:
    """Alerts the twin observed (ground truth): a late timetabled train and a train held for a resource."""
    badges = world.register.value("ui.delay_badge_thresholds_min")
    amber, red, red_premium, yellow = (
        float(badges[k]) * SECONDS_PER_MIN for k in ("amber", "red", "red_premium", "yellow")
    )
    origin = day_start(day)
    names = {p.run_id.replace(" ", "_"): p.run_id for p in plans}
    klass = {p.run_id: _class_of(p) for p in plans}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    spells: dict[str, tuple[float, str, float] | None] = {}
    for ev in sim.events:
        rid = names.get(ev.train_run_id or "")
        if rid is None or ev.location is None:
            continue
        t = (ev.event_time - origin).total_seconds()
        km = ev.location.km if ev.location.km is not None else 0.0
        if ev.event_type is EventType.DELAY_RECORD and rid not in seen:
            delay = float(ev.payload["delay_s"])
            if delay >= amber:
                seen.add(rid)
                limit = red_premium if klass[rid] == "premium" else red
                place = ev.payload["station"]
                text = f"{rid} is {round(delay / SECONDS_PER_MIN)} min late at {place}"
                out.append(_alert("delay", "P1" if delay >= limit else "P2", rid, place, km, t, text))
        elif ev.event_type is EventType.TRAIN_STATE:
            spell = spells.get(rid)
            if ev.payload["status"] == WAITING and spell is None:
                spells[rid] = (t, ev.payload["station"], km)
            elif ev.payload["status"] != WAITING and spell is not None:
                spells[rid] = None
                held = t - spell[0]
                if held >= yellow:
                    text = f"{rid} was held {round(held / SECONDS_PER_MIN)} min at {spell[1]} for a resource"
                    out.append(
                        _alert(
                            "held", "P2" if held >= amber else "P3", rid, spell[1], spell[2], spell[0], text
                        )
                    )
    out.sort(key=lambda a: (a["time_s"], a["trains"]))
    for i, a in enumerate(out, 1):
        a["id"] = f"A-{i:03d}"
    return out


def _verification(world: run.World, sim: Sim, day: dt.date) -> dict[str, Any]:
    """The rule auditor over the replay of the option (the catalogue the verifier gates use)."""
    net = network.load(world.layout.network_version)
    metas = {
        t.plan.run_id: TrainMeta(
            kind=t.plan.kind,
            length_m=t.plan.template.length_m,
            electric=t.plan.template.loco.traction == "electric",
            booked_dep={n.op: n.dep for n in t.plan.nodes if n.stop} if t.plan.timetabled else {},
        )
        for t in sim.trains
    }
    result = audit(sim.events, metas, net, day_start(day))
    rules = [
        {"rule_id": r.rule_id, "status": r.status.value, "violations": len(r.violations)}
        for r in result.report.results
    ]
    return {
        "status": "PASS" if result.executed_violations == 0 else "FAIL",
        "gate": "rule auditor over the replayed option (FR-VER-12)",
        "violations": result.executed_violations,
        "warnings": result.warnings,
        "rules": rules,
    }


def _impacts(
    plans: list[Plan],
    actual: dict[str, list[list[float]]],
    proposed: dict[str, list[list[float]]],
    weights: dict[str, float],
) -> list[dict[str, Any]]:
    """Per changed train: the exit time before and after, weighted by its class (worst first)."""
    by_id = {p.run_id: p for p in plans}
    rows = []
    for rid, after in proposed.items():
        before = actual.get(rid, [])
        if not before or not after:
            continue
        plan = by_id[rid]
        klass = _class_of(plan)
        rows.append(
            {
                "id": rid,
                "name": plan.name,
                "class": klass,
                "weight": weights[klass],
                "delta_s": after[-1][0] - before[-1][0],
                "exit_before_s": before[-1][0],
                "exit_after_s": after[-1][0],
            }
        )
    return sorted(rows, key=lambda r: (-r["weight"] * abs(r["delta_s"]), r["id"]))


def _demo_train(plans: list[Plan]) -> Plan:
    """The first freight train entering at or after midday (deterministic)."""
    freight = sorted((p for p in plans if p.kind == "freight"), key=lambda p: (p.entry_s, p.run_id))
    midday = SECONDS_PER_MIN * SECONDS_PER_MIN * 12
    return next((p for p in freight if p.entry_s >= midday), freight[-1])


def document(world: run.World, day: dt.date, plans: list[Plan], sim: Sim, report: Any) -> dict[str, Any]:
    """The chart document of a run without a preview: stations, trains (actual and booked lines), alerts,
    the mimic. ``chart_data`` adds the demo preview; the what-if export (issue #172) adds its options."""
    actual = _lines(sim, day)
    weights = {k: float(v) for k, v in world.register.value("policy.weights").items()}
    hard_rules = list(world.register.value("policy.hard_rules"))
    booked_by = {p.run_id: _booked(world, p) for p in plans}
    return {
        "format": FORMAT,
        "version": VERSION,
        "network_version": world.layout.network_version,
        "day": day.isoformat(),
        "scenario_id": report.scenario_id,
        "trace_hash": report.trace_hash,
        "completed": report.completed,
        "ui": {
            "delay_badge_thresholds_min": world.register.value("ui.delay_badge_thresholds_min"),
            "palette_coa": world.register.value("ui.palette.coa"),
        },
        "policy": {
            "weights": weights,
            "hard_rules": hard_rules,
            "weights_in_explanations": bool(world.register.value("policy.weights_in_explanations")),
        },
        "stations": _stations(world),
        "trains": [
            {
                "id": p.run_id,
                "name": p.name,
                "kind": p.kind,
                "category": p.category,
                "class": _class_of(p),
                "weight": weights[_class_of(p)],
                "load": _load(p),
                "entry_delay_s": _delays(booked_by[p.run_id], actual.get(p.run_id, []))[0],
                "exit_delay_s": _delays(booked_by[p.run_id], actual.get(p.run_id, []))[1],
                "actual": actual.get(p.run_id, []),
                "booked": booked_by[p.run_id],
            }
            for p in plans
        ],
        "alerts": _alerts(sim, day, plans, world),
        "mimic": mimic_data(world, sim, [p.run_id for p in plans]),
    }


def chart_data(world: run.World, day: dt.date) -> dict[str, Any]:
    plans, _, inst = run.plans_for(world, day)
    report, sim = run.run_day(world, day, plans=plans, keep_events=True)
    actual = _lines(sim, day)
    hold_s = float(world.register.value("ui.preview_demo_hold_min")) * SECONDS_PER_MIN
    held = _demo_train(plans)
    variant = [dataclasses.replace(p, entry_s=p.entry_s + hold_s) if p is held else p for p in plans]
    _, alt_sim = run.run_day(world, day, plans=variant, keep_events=True, scenario_id=f"{inst}-preview")
    proposed = {rid: pts for rid, pts in _lines(alt_sim, day).items() if pts != actual.get(rid)}
    hold_min = round(hold_s / SECONDS_PER_MIN)
    entry = held.nodes[0].op
    weights = {k: float(v) for k, v in world.register.value("policy.weights").items()}
    impacts = _impacts(plans, actual, proposed, weights)
    hard_rules = list(world.register.value("policy.hard_rules"))
    return {
        **document(world, day, plans, sim, report),
        "preview": {
            "id": "R-DEMO-1",
            "demo": True,
            "context": f"Entry of {held.run_id} at {entry}",
            "options": [
                {
                    "id": "A",
                    "title": f"Hold {held.run_id} at {entry} for {hold_min} min",
                    "orders": [f"SM/{entry}: hold {held.run_id} for {hold_min} min before admitting it"],
                    "trains": proposed,
                    "impacts": impacts,
                    "weighted_delay_delta_min": round(
                        sum(i["weight"] * i["delta_s"] for i in impacts) / SECONDS_PER_MIN, 1
                    ),
                    "verification": _verification(world, alt_sim, day),
                    "explanation": {
                        "why": (
                            f"Illustrates the card: {held.run_id} is held at {entry} for {hold_min} min and "
                            f"the twin replays the day. {len(impacts)} trains change."
                        ),
                        "why_not": "Do nothing keeps the twin run as it is (the baseline of the impacts).",
                        "weights": weights,
                        "hard_rules": [
                            {
                                "rule": r,
                                "applied": False,
                                "note": "not triggered: no relief train is affected",
                            }
                            for r in hard_rules
                        ],
                    },
                    "confidence": None,
                    "assumptions": [
                        "Twin ground truth: no prediction uncertainty is modelled here.",
                        "A demo hold, not an optimiser output (issue #86).",
                    ],
                }
            ],
            "valid_until_s": round(held.entry_s),
        },
    }
