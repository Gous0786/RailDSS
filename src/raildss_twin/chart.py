"""Control-chart data for the web UI (spec FR-UI-02, FR-UI-03; issue #86).

``chart_data`` replays a real day in the twin and returns what the time-distance chart draws:

* **stations** of the published network (block stations and halts) with their chainage and loops;
* per train, the **actual** line (the twin's ground-truth position every state period) and, for
  timetabled trains, the **booked** WTT line;
* a **demo recommendation preview**: one option ("hold a freight train at its entry for
  ``ui.preview_demo_hold_min``") replayed in the twin, with the lines of every train it changes. The
  do-nothing baseline is the actual run. It is labelled DEMO until the optimiser produces real
  recommendations.

Times are seconds since the start of the day; distances are km from KTV.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
from typing import Any

from raildss_common.envelope import EventType
from raildss_twin import run
from raildss_twin.plans import Plan, day_start
from raildss_twin.sim import Sim

FORMAT = "raildss-chart"
VERSION = 1
SECONDS_PER_MIN = 60
KM_DIGITS = 3  # rounding of chainage in the export (metres)


def _lines(sim: Sim, day: dt.date) -> dict[str, list[list[float]]]:
    """``run id -> [[t, km], ...]`` from the ground-truth train states (one per state period)."""
    origin = day_start(day)
    names = {t.plan.run_id.replace(" ", "_"): t.plan.run_id for t in sim.trains}
    out: dict[str, list[list[float]]] = {}
    for ev in sim.events:
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


def _demo_train(plans: list[Plan]) -> Plan:
    """The first freight train entering at or after midday (deterministic)."""
    freight = sorted((p for p in plans if p.kind == "freight"), key=lambda p: (p.entry_s, p.run_id))
    midday = SECONDS_PER_MIN * SECONDS_PER_MIN * 12
    return next((p for p in freight if p.entry_s >= midday), freight[-1])


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
        "stations": _stations(world),
        "trains": [
            {
                "id": p.run_id,
                "name": p.name,
                "kind": p.kind,
                "category": p.category,
                "actual": actual.get(p.run_id, []),
                "booked": _booked(world, p),
            }
            for p in plans
        ],
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
                }
            ],
        },
    }
