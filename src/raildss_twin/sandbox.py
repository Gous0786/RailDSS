"""Sandbox replay and gate G3 (spec FR-VER-05, FR-TWIN-16, §9.3; issue #79).

``replay`` forks the twin at ``start_s`` of a day, applies a candidate plan from there, replays the
horizon deterministically (P50 durations: the twin has no perturbations here) and judges the result:

* **Fork.** The twin is re-simulated under BL-0 from the start of the day to ``start_s``, which gives
  its exact state at the fork; nothing before the fork is changed. When a DSS snapshot is given (the
  occupancy view of ``S_t``, :class:`raildss_twin.displib.Snapshot`), it is compared with that state
  and the trains that differ are reported in ``snapshot_mismatch``. Injecting an estimated state that
  diverges from the twin's own is the next step once ``S_t`` is published (#71).
* **Plan.** A :class:`SandboxPlan` holds trains at stations and chooses station lines (the twin
  applies them as :class:`~raildss_twin.sim.Directives` from the fork on) and carries the context the
  rules judge against: actions, maintenance blocks and frozen decisions.
* **Verdict.** The offline rule auditor (#78) runs over the replayed ground truth with that context.
  FAIL-level violations inside the window, planned steps the twin refused (with the rule), deadlocks
  and stuck trains fail G3; WARN-level ones give PASS_WITH_WARN.
* **KPIs.** The same window replayed without the plan (do nothing, BL-0) is the baseline; the result
  carries both and their difference.

The same plan, day, fork time and register give a byte-identical result (``trace_hash`` included).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, Field

from raildss_common.envelope import EventType
from raildss_params import Register
from raildss_twin import displib, run
from raildss_twin.plans import day_start
from raildss_twin.sim import Deadlock, Directives, RunReport, Sim, token
from raildss_verifier import network
from raildss_verifier.auditor import TrainMeta, audit
from raildss_verifier.model import Action, CandidatePlan, FrozenCall, MaintenanceBlock, Status

SECONDS_PER_MIN = 60


class Hold(BaseModel):
    run_id: str
    station: str
    until_s: float


class LineChoice(BaseModel):
    run_id: str
    station: str
    line_no: int


class SandboxPlan(BaseModel):
    plan_id: str
    holds: list[Hold] = Field(default_factory=list)
    lines: list[LineChoice] = Field(default_factory=list)
    actions: list[Action] = Field(default_factory=list)
    maintenance: list[MaintenanceBlock] = Field(default_factory=list)
    frozen: list[FrozenCall] = Field(default_factory=list)


class G3Violation(BaseModel):
    rule_id: str
    trains: list[str]
    time_s: float
    detail: str
    source: Literal["replay", "plan_step", "deadlock", "stuck"]


class G3Result(BaseModel):
    plan_id: str
    day: dt.date
    start_s: float
    end_s: float
    status: Literal["PASS", "PASS_WITH_WARN", "FAIL"]
    violations: list[G3Violation]
    warnings: int
    kpis: dict[str, dict[str, float]]  # baseline / plan / delta
    trace_hash: str
    baseline_trace_hash: str
    snapshot_mismatch: list[str] = Field(default_factory=list)


def train_meta(sim: Sim) -> dict[str, TrainMeta]:
    """What the auditor needs about each train that the trace does not carry."""
    return {
        token(t.plan.run_id): TrainMeta(
            kind=t.plan.kind,
            length_m=t.plan.template.length_m,
            electric=t.plan.template.loco.traction == "electric",
            booked_dep={n.op: n.dep for n in t.plan.nodes if n.stop} if t.plan.timetabled else {},
        )
        for t in sim.trains
    }


def kpis(sim: Sim, day: dt.date, start_s: float, end_s: float) -> dict[str, float]:
    """Window KPIs: passenger arrival delay (total and worst, min) and trains that left the section."""
    origin = day_start(day)
    delays = [
        max(0.0, float(ev.payload["delay_s"]))
        for ev in sim.events
        if ev.event_type is EventType.DELAY_RECORD
        and ev.payload.get("kind") == "arrival"
        and start_s <= (ev.event_time - origin).total_seconds() <= end_s
    ]
    exited = sum(1 for t in sim.trains if t.exit_s is not None and start_s <= t.exit_s <= end_s)
    return {
        "passenger_arrival_delay_min": round(sum(delays) / SECONDS_PER_MIN, 1),
        "max_arrival_delay_min": round(max(delays, default=0.0) / SECONDS_PER_MIN, 1),
        "trains_exited": float(exited),
    }


def _mismatch(snapshot: displib.Snapshot, sim: Sim) -> list[str]:
    forked = displib.snapshot_from_occupancy(sim.occupancy_log, snapshot.time_s, snapshot.network_version)
    mine = {t.run_id: [o.resource for o in t.occupations] for t in forked.trains}
    theirs = {t.run_id: [o.resource for o in t.occupations] for t in snapshot.trains}
    return sorted(rid for rid in mine.keys() | theirs.keys() if mine.get(rid) != theirs.get(rid))


def _deadlock_violations(report: RunReport, start_s: float) -> list[G3Violation]:
    out = []
    for d in report.deadlocks:
        if isinstance(d, Deadlock) and d.time_s >= start_s:
            out.append(
                G3Violation(
                    rule_id="VER-DLK-01",
                    trains=sorted(t.run_id for t in d.trains),
                    time_s=d.time_s,
                    detail="deadlock: " + "; ".join(f"{t.run_id} at {t.location}" for t in d.trains),
                    source="deadlock",
                )
            )
    return out


def replay(
    world: run.World,
    day: dt.date,
    start_s: float,
    plan: SandboxPlan,
    *,
    horizon_s: float | None = None,
    snapshot: displib.Snapshot | None = None,
    net: network.Network | None = None,
    register: Register | None = None,
) -> G3Result:
    """Fork at ``start_s``, replay ``plan`` over the horizon, and judge it (G3, deterministic)."""
    reg = register or world.register
    horizon = horizon_s if horizon_s is not None else float(reg.value("cd.horizon_min")) * SECONDS_PER_MIN
    end_s = start_s + horizon
    net = net or network.load(world.layout.network_version)
    plans, _, inst = run.plans_for(world, day)
    base_report, base = run.run_day(
        world, day, plans=plans, until_s=end_s, keep_events=True, scenario_id=f"{inst}-g3-baseline"
    )
    # plans name trains as the trace does (no spaces); the twin keys them by run id
    rid = {token(p.run_id): p.run_id for p in plans} | {p.run_id: p.run_id for p in plans}
    directives = Directives(
        holds={(rid.get(h.run_id, h.run_id), h.station): h.until_s for h in plan.holds},
        lines={(rid.get(c.run_id, c.run_id), c.station): c.line_no for c in plan.lines},
        from_s=start_s,
    )
    report, sim = run.run_day(
        world,
        day,
        plans=plans,
        until_s=end_s,
        keep_events=True,
        directives=directives,
        scenario_id=f"{inst}-g3-{plan.plan_id}",
    )
    context = CandidatePlan(
        plan_id=plan.plan_id,
        network_version=net.version,
        trains=[],
        actions=plan.actions,
        maintenance=plan.maintenance,
        frozen=plan.frozen,
    )
    result = audit(
        sim.events, train_meta(sim), net, day_start(day), register=reg, plan_id=plan.plan_id, context=context
    )
    violations: list[G3Violation] = []
    warnings = 0
    for r in result.report.results:
        inside = [v for v in r.violations if start_s <= v.time_s <= end_s]
        if r.status in (Status.FAIL, Status.ERROR):
            if r.status is Status.ERROR:
                violations.append(
                    G3Violation(
                        rule_id=r.rule_id,
                        trains=[],
                        time_s=start_s,
                        detail=f"rule error: {r.error}",
                        source="replay",
                    )
                )
            violations += [
                G3Violation(
                    rule_id=r.rule_id, trains=v.trains, time_s=v.time_s, detail=v.detail, source="replay"
                )
                for v in inside
            ]
        elif r.status is Status.WARN:
            warnings += len(inside)
    violations += [
        G3Violation(
            rule_id=x.rule, trains=[token(x.run_id)], time_s=x.time_s, detail=x.reason, source="plan_step"
        )
        for x in sim.directive_refusals
    ]
    violations += _deadlock_violations(report, start_s)
    violations += [
        G3Violation(
            rule_id="VER-DLK-01", trains=[s.run_id], time_s=end_s, detail=f"stuck: {s.reason}", source="stuck"
        )
        for s in report.stuck
    ]
    violations.sort(key=lambda v: (v.time_s, v.rule_id, v.trains))
    base_k, plan_k = kpis(base, day, start_s, end_s), kpis(sim, day, start_s, end_s)
    status: Literal["PASS", "PASS_WITH_WARN", "FAIL"] = (
        "FAIL" if violations else "PASS_WITH_WARN" if warnings else "PASS"
    )
    return G3Result(
        plan_id=plan.plan_id,
        day=day,
        start_s=start_s,
        end_s=end_s,
        status=status,
        violations=violations,
        warnings=warnings,
        kpis={"baseline": base_k, "plan": plan_k, "delta": _delta(plan_k, base_k)},
        trace_hash=report.trace_hash,
        baseline_trace_hash=base_report.trace_hash,
        snapshot_mismatch=_mismatch(snapshot, base) if snapshot is not None else [],
    )


def _delta(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, Any]:
    return {k: round(a[k] - b[k], 1) for k in sorted(a)}
