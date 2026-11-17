"""KPI evaluation runs in the twin (spec FR-KPI-01, FR-KPI-05; issue #81).

Builds the per-train table the KPI engine (:mod:`raildss_dss.kpi`) needs from the twin's plans: the
precedence class, booked entry and exit, and for freight the unimpeded running time from the
calibrated running-time model. It then replays the arms of a comparison in the twin: BL-0, and a
candidate plan applied as :class:`~raildss_twin.sim.Directives`. The DSS arm of a scenario or a day is
such a candidate until the decision loop (#84) produces its own plans.
"""

from __future__ import annotations

import datetime as dt
import itertools
from typing import Any

from raildss_dss.kpi import KpiReport, TrainInfo, compare_runs, compute
from raildss_twin import run
from raildss_twin.plans import Plan, day_start
from raildss_twin.sim import Directives, token


def free_run_s(world: run.World, plan: Plan) -> float:
    """Unimpeded running time through the section: calibrated legs between block stations."""
    stations = [n.op for n in plan.nodes if not world.layout.ops[n.op].is_halt]
    last = len(stations) - 2
    return sum(
        world.timing.leg(plan, a, b, k == 0, k == last)
        for k, (a, b) in enumerate(itertools.pairwise(stations))
    )


def train_table(world: run.World, plans: list[Plan]) -> dict[str, TrainInfo]:
    table = {}
    for p in plans:
        first, last = p.nodes[0], p.nodes[-1]
        table[token(p.run_id)] = TrainInfo(
            run_id=token(p.run_id),
            kind=p.kind,
            category=p.category,
            direction="DN" if world.layout.km(last.op) > world.layout.km(first.op) else "UP",
            booked_entry_s=first.arr if p.timetabled else None,
            booked_exit_s=last.arr if p.timetabled else None,
            free_run_s=None if p.timetabled else free_run_s(world, p),
        )
    return table


def run_arm(
    world: run.World,
    day: dt.date,
    arm: str,
    scope: str,
    *,
    directives: Directives | None = None,
    seed: int | None = None,
    day_instance: str | None = None,
) -> KpiReport:
    """Replay the day under BL-0 (with a candidate's directives, if any) and compute its KPIs."""
    plans, _, _ = run.plans_for(world, day, day_instance)
    _, sim = run.run_day(
        world, day, plans=plans, seed=seed, day_instance=day_instance, keep_events=True, directives=directives
    )
    return compute(
        sim.events,
        train_table(world, plans),
        day_start(day),
        scope=scope,
        arm=arm,
        network_version=world.layout.network_version,
        register=world.register,
    )


def day_comparison(world: run.World, day: dt.date, candidate: Directives, **kw: Any) -> dict[str, Any]:
    """FR-KPI-05 for one day: the candidate (DSS arm) against BL-0 (do nothing in the twin)."""
    scope = kw.pop("scope", f"day {day.isoformat()}")
    baseline = run_arm(world, day, "BL-0", scope, **kw)
    dss = run_arm(world, day, "DSS", scope, directives=candidate, **kw)
    return compare_runs(dss, [baseline])
