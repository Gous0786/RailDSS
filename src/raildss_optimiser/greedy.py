"""Greedy priority dispatcher (spec FR-OPT-02/03/07/19/20, §8.4; OD-04, OD-25; issue #75).

The dispatcher forks the twin's movement engine at ``t`` and lets it run the horizon (``cd.horizon_min``)
under a **priority policy** instead of BL-0's first come, first served:

* **Priority** (FR-OPT-02): every queue for a block line, sub-block or station line serves the train with
  the highest ``policy.weights`` class first; relief trains go before all (``policy.hard_rules``); ties go
  to the earliest request (earliest arrival). A lower-priority train therefore waits on a station line,
  the nearest feasible station, while the higher-priority one passes.
* **Line choice** (FR-OPT-19): each train takes the nearest free line the Connections permit, its own
  direction before the reversible MID line on KTV-VZM, as the engine does.
* **Single-line working**: closed segment lines (maintenance windows, :class:`raildss_twin.sim.Closure`)
  are not used while closed; a train waits at its station while every line it may use is closed.
* **Boundary acceptance** (FR-OPT-20): a train is held at the last block station before its exit while
  the exit's acceptance window is closed, and never exits into it.
* **Frozen decisions** (FR-OPT-07): departures inside the commit window keep their line and time.

The resulting movements become a verifier :class:`~raildss_verifier.model.CandidatePlan` (the auditor's
trace reader) and are checked by gate G2 (the rule catalogue). :func:`recommend` turns the plan into a
recommendation for each conflict DS-5 detected; :class:`RollingDispatcher` re-plans on the
``opt.replan_triggers`` and at least every ``opt.replan_max_interval_min`` with the
``opt.freeze_window_min`` freeze. The same code is the CP-SAT warm start, the fallback and the
"controller practice" baseline (FR-OPT-03).

The fork re-simulates the day from midnight (the twin's state at ``t``); on D0 it equals the DSS estimate
``S_t`` (#71). Injecting a diverging estimated state is future work, as for the G3 sandbox (#79).
"""

from __future__ import annotations

import datetime as dt
import itertools
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from raildss_params import Register
from raildss_twin import run
from raildss_twin.layout import pair_key
from raildss_twin.plans import Plan, day_start
from raildss_twin.sandbox import train_meta
from raildss_twin.sim import Closure, Directives, RunReport, token
from raildss_verifier import engine, network
from raildss_verifier.auditor import plan_from_trace
from raildss_verifier.model import CandidatePlan, FrozenCall, MaintenanceBlock, Overall, Report

SECONDS_PER_MIN = 60
RELIEF = "relief"


@dataclass(frozen=True)
class Decision:
    """A departure decision of the plan: the train leaves ``station`` from ``line_no`` at ``dep_s``."""

    run_id: str
    station: str
    line_no: int
    dep_s: float


@dataclass(frozen=True)
class Hold:
    run_id: str
    station: str
    until_s: float
    held_s: float  # beyond the booked departure (passenger) or the arrival (freight)


@dataclass
class GreedyPlan:
    plan_id: str
    start_s: float
    end_s: float
    plan: CandidatePlan
    g2: Report
    decisions: list[Decision]
    holds: list[Hold]
    stuck: list[str]
    runtime_s: float
    report: RunReport

    @property
    def g2_fails(self) -> int:
        return sum(r.status.value in ("FAIL", "ERROR") for r in self.g2.results)

    @property
    def ok(self) -> bool:
        return self.g2.overall is not Overall.FAIL and not self.stuck


class ConflictLike(Protocol):
    type: int
    trains: tuple[str, ...]
    resource: str
    time: dt.datetime


@dataclass
class Recommendation:
    """What the greedy plan does about one detected conflict (DS-9 presents it, FR-HITL)."""

    conflict_type: int
    trains: tuple[str, ...]
    resource: str
    time: dt.datetime
    status: str  # RESOLVED | NO_FEASIBLE_RESOLUTION
    actions: list[str] = field(default_factory=list)
    plan_id: str = ""


class GreedyDispatcher:
    """FR-OPT-02: a complete plan for the horizon, resolving conflicts by priority."""

    def __init__(self, world: run.World, register: Register | None = None) -> None:
        self.world = world
        self.reg = register or world.register
        self.net = network.load(world.layout.network_version)
        self.weights = {k: float(v) for k, v in self.reg.value("policy.weights").items()}
        self.relief_first = RELIEF + "_trains_first" in list(self.reg.value("policy.hard_rules"))
        self.horizon_s = float(self.reg.value("cd.horizon_min")) * SECONDS_PER_MIN
        self.block_of = {pair_key(a, b): blk.id for (a, b), blk in world.layout.block_by_pair.items()}

    # ------------------------------------------------------------------ policy
    def weight(self, plan: Plan, classes: Mapping[str, str]) -> float:
        if plan.kind == "freight":
            return self.weights.get("freight_other", 1.0)
        return self.weights.get(classes.get(plan.run_id.partition("@")[0], "passenger"), 1.0)

    def priorities(self, plans: Iterable[Plan]) -> dict[str, int]:
        """Request priority per run id: lower is served first (simpy), so minus the weight."""
        classes = {str(s.train_no): str(s.priority_class) for s in self.world.tables["service"]}  # type: ignore[attr-defined]
        top = max(self.weights.values(), default=1.0) + 1
        out = {}
        for p in plans:
            relief = self.relief_first and RELIEF in p.name.lower()
            out[p.run_id] = -round(top if relief else self.weight(p, classes))
        return out

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        day: dt.date,
        start_s: float,
        *,
        horizon_s: float | None = None,
        closures: Sequence[Closure] = (),
        exit_windows: Mapping[str, list[tuple[float, float]]] | None = None,
        frozen: Sequence[Decision] = (),
        plan_id: str = "greedy",
        policy_from_s: float | None = None,
        seed: int | None = None,
    ) -> GreedyPlan:
        """Plan from ``start_s``. ``policy_from_s`` (rolling horizon) is when the dispatcher took over: the
        history since then ran under the policy, not BL-0. Trains with a frozen decision go first, so no
        other train can take their line before the frozen departure."""
        end_s = start_s + (horizon_s if horizon_s is not None else self.horizon_s)
        plans, _, inst = run.plans_for(self.world, day)
        rid = {token(p.run_id): p.run_id for p in plans}
        priority = self.priorities(plans)
        first = min(priority.values(), default=0) - 1
        for f in frozen:
            priority[rid.get(f.run_id, f.run_id)] = first
        directives = Directives(
            holds={(rid.get(f.run_id, f.run_id), f.station): f.dep_s for f in frozen},
            lines={(rid.get(f.run_id, f.run_id), f.station): f.line_no for f in frozen},
            from_s=start_s if policy_from_s is None else min(policy_from_s, start_s),
            priority=priority,
            closures=list(closures),
            exit_windows=dict(exit_windows or {}),
        )
        t0 = time.perf_counter()
        report, sim = run.run_day(
            self.world,
            day,
            seed=seed,
            plans=plans,
            until_s=end_s,
            keep_events=True,
            directives=directives,
            scenario_id=f"{inst}-greedy-{plan_id}",
        )
        context = CandidatePlan(
            plan_id=plan_id,
            network_version=self.net.version,
            trains=[],
            maintenance=[
                MaintenanceBlock(
                    block_id=self.block_of[c.segment], line_id=c.line_id, start_s=c.start_s, end_s=c.end_s
                )
                for c in closures
            ],
            frozen=[
                FrozenCall(run_id=f.run_id, op=f.station, line_no=f.line_no, dep_s=f.dep_s) for f in frozen
            ],
        )
        cand = plan_from_trace(
            sim.events,
            train_meta(sim),
            self.net,
            day_start(day),
            register=self.reg,
            plan_id=plan_id,
            context=context,
        )
        g2 = engine.check(cand, self.net, register=self.reg)
        runtime = time.perf_counter() - t0
        decisions, holds = [], []
        for tr in cand.trains:
            for c in tr.calls[:-1]:  # the last call is the exit, not a departure decision
                if start_s <= c.dep_s < end_s:
                    decisions.append(Decision(tr.run_id, c.op, c.line_no, c.dep_s))
                    base = c.booked_dep_s if c.booked_dep_s is not None else c.arr_s
                    if c.stop and c.dep_s - base > 0:
                        holds.append(Hold(tr.run_id, c.op, c.dep_s, c.dep_s - base))
        stuck = sorted(
            {s.run_id for s in report.stuck} | {t.run_id for d in report.deadlocks for t in d.trains}
        )
        return GreedyPlan(plan_id, start_s, end_s, cand, g2, decisions, holds, stuck, runtime, report)

    # ------------------------------------------------------------------ scenarios
    def closures_of(self, scenario: Any) -> list[Closure]:
        """Line blocks of a v0 scenario as closures, over the upper bound of their duration."""
        out = []
        for d in scenario.disruptions:
            if d.type == "line_block" and d.block_id in self.world.layout.blocks:
                blk = self.world.layout.blocks[d.block_id]
                end = d.start_s + float(d.duration_min.high_min) * SECONDS_PER_MIN
                out.append(Closure(pair_key(blk.from_op, blk.to_op), d.line_id, d.start_s, end))
        return out

    def plan_scenario(self, scenario: Any, start_s: float) -> GreedyPlan:
        """Plan a v0 scenario (``raildss_twin.scenario``) on its day and seed, with its line blocks."""
        return self.plan(
            scenario.wtt_date,
            start_s,
            closures=self.closures_of(scenario),
            seed=scenario.seed,
            plan_id=scenario.id.lower(),
        )


# --------------------------------------------------------------------------- recommendations
def recommend(
    conflicts: Iterable[ConflictLike], plan: GreedyPlan, origin: dt.datetime
) -> list[Recommendation]:
    """The greedy plan's resolution of each detected conflict: who goes first, and the holds."""
    trains = {t.run_id: t for t in plan.plan.trains}
    holds = {(h.run_id, h.station): h for h in plan.holds}

    def clock(s: float) -> str:
        return (origin + dt.timedelta(seconds=s)).astimezone(dt.UTC).strftime("%H:%M")

    out = []
    for c in conflicts:
        rec = Recommendation(c.type, tuple(c.trains), c.resource, c.time, "RESOLVED", plan_id=plan.plan_id)
        if any(r in plan.stuck for r in c.trains) or any(r not in trains for r in c.trains):
            rec.status = "NO_FEASIBLE_RESOLUTION"
            rec.actions.append("manual decision needed: the greedy plan cannot move every train involved")
            out.append(rec)
            continue
        if "~" in c.resource:  # a segment: the order of entry decides
            stations = set(c.resource.split("~"))
            entries = []
            for r in c.trains:
                runs = [
                    (run.enter_s, run.line_id, run.block_id)
                    for run in trains[r].runs
                    if set(run.block_id.split("-")) == stations
                ]
                if runs:
                    entries.append((min(runs), r))
            for k, ((enter, line, block), r) in enumerate(sorted(entries)):
                origin_op = block.split("-")[0]
                held = holds.get((r, origin_op))
                what = f"{r} enters {block} on line {line} at {clock(enter)}"
                if k == 0:
                    rec.actions.append(f"{what} (first)")
                elif held is not None:
                    rec.actions.append(
                        f"hold {r} at {origin_op} {held.held_s / SECONDS_PER_MIN:.0f} min; {what}"
                    )
                else:
                    rec.actions.append(f"{what} (after the first)")
        else:  # a station: the lines the trains take
            for r in c.trains:
                calls = [x for x in trains[r].calls if x.op == c.resource]
                for x in calls:
                    rec.actions.append(
                        f"receive {r} at {c.resource} on line {x.line_no} ({clock(x.arr_s)}-{clock(x.dep_s)})"
                    )
        out.append(rec)
    return out


# --------------------------------------------------------------------------- rolling horizon
@dataclass
class RollingDispatcher:
    """FR-OPT-07: re-plan on triggers and at least every ``opt.replan_max_interval_min``; freeze the
    decisions inside ``opt.freeze_window_min`` (a departure that is history never changes either)."""

    dispatcher: GreedyDispatcher
    day: dt.date
    plans: list[GreedyPlan] = field(default_factory=list)
    frozen: dict[tuple[str, str], Decision] = field(default_factory=dict)

    def __post_init__(self) -> None:
        reg = self.dispatcher.reg
        self.max_interval_s = float(reg.value("opt.replan_max_interval_min")) * SECONDS_PER_MIN
        self.freeze_s = float(reg.value("opt.freeze_window_min")) * SECONDS_PER_MIN
        self.triggers = set(reg.value("opt.replan_triggers"))

    def due(self, now_s: float, trigger: str | None = None) -> bool:
        if not self.plans or (trigger is not None and trigger in self.triggers):
            return True
        return now_s - self.plans[-1].start_s >= self.max_interval_s

    def step(self, now_s: float, trigger: str | None = None, **kw: Any) -> GreedyPlan | None:
        """Re-plan if due; the decisions of the last plan inside the commit window are frozen first."""
        if not self.due(now_s, trigger):
            return None
        if self.plans:
            for d in self.plans[-1].decisions:
                if now_s <= d.dep_s < now_s + self.freeze_s:
                    self.frozen.setdefault((d.run_id, d.station), d)
        keep = [d for d in self.frozen.values() if d.dep_s >= now_s]
        since = self.plans[0].start_s if self.plans else now_s
        plan = self.dispatcher.plan(
            self.day, now_s, frozen=keep, plan_id=f"greedy-{len(self.plans)}", policy_from_s=since, **kw
        )
        self.plans.append(plan)
        return plan

    @property
    def max_gap_s(self) -> float:
        starts = [p.start_s for p in self.plans]
        return max((b - a for a, b in itertools.pairwise(starts)), default=0.0)
