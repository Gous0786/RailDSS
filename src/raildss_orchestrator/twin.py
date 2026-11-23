"""The loop on a twin day: observation feed, DSS chain and planner (issue #84).

* :class:`TwinFeed` plays the ``obs.*`` records of a twin day by delivery time, as a live feed would. When
  an accepted order changes the world, the day is re-simulated under the new directives and the records
  not yet delivered are replaced (what was delivered is unchanged: the twin is deterministic).
* :class:`TwinDss` is the DSS chain of #70-#73 run incrementally: ingestion, state, ETAs, conflicts.
* :class:`TwinPlanner` builds the options on the twin's fork at ``t``: the greedy dispatcher (#75), the
  CP-SAT model (#76) warm-started with it, G1 on the solver's answer (#77), G2 (#78) and the G3 sandbox
  replay (#79), which also gives each option's KPI delta against the baseline BL-0 (#81).

Planning forks the twin (the greedy dispatcher's own state): on a day nobody has acted on it equals the
DSS estimate ``S_t``; injecting a diverging estimate is future work, as for G3 (#79). The CP-SAT instance
does not model closures: a plan that uses a closed line fails G3 and is never released.

Disruption options (issue #156, FR-OPT-11): while an incident or closure is in force in the horizon, the
planner adds a **re-order / re-cross** option (the train the plan holds longest is served first, so the
crossing or overtaking happens at another station) and, policy-gated, a **short-termination or cancellation**
of the passenger train held longest (ranked with a penalty). When an incident is announced with an uncertain
restoration time (ETR), it builds **contingent plans** for ETR + each ``contingent_etr_min`` (the cycle's
plan is the one for the ETR), each verified in its own scenario, the switch rule naming the decision time.
"""

from __future__ import annotations

import datetime as dt
import itertools
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

from raildss_dss.conflicts import Detector, occupations
from raildss_dss.ingest import Ingestor
from raildss_dss.predict import DegradedPredictor, Predictor, ProcessTimes
from raildss_dss.state import PlanDirectory, StateEstimator
from raildss_optimiser import cpsat
from raildss_optimiser.greedy import Decision, GreedyDispatcher, GreedyPlan, Hold
from raildss_params import Register
from raildss_twin import displib, run, sandbox
from raildss_twin.displib_check import schedule_from_trace
from raildss_twin.kpi_eval import run_arm
from raildss_twin.observe import Observer
from raildss_twin.plans import day_start
from raildss_twin.sim import Closure, Directives, token
from raildss_verifier import gates
from raildss_verifier.model import FrozenCall, MaintenanceBlock

from .model import Candidate, Contingent, DssView, PlanContext, PlanSet

SECONDS_PER_MIN = 60
HINT_END_S = 36 * 3600  # the hint run goes to the end of the day: DISPLIB paths run to the exit
MIN_HOLD_S = 1.0  # a CP-SAT departure later than booked by more than this is a hold


# --------------------------------------------------------------------------- the world
def standing_directives(
    world: run.World,
    day: dt.date,
    *,
    closures: Sequence[Closure],
    exit_windows: Mapping[str, list[tuple[float, float]]],
    holds: Mapping[tuple[str, str], float],
    lines: Mapping[tuple[str, str], int],
    incidents: Sequence[Any] = (),
    entry_delays: Mapping[str, float] | None = None,
    terminations: Mapping[str, str] | None = None,
) -> Directives:
    """What holds in the world: closures, closed exits, incidents, late entries and the accepted orders,
    short-terminations and cancellations included (BL-0 does the rest)."""
    plans, _, _ = run.plans_for(world, day)
    rid = {token(p.run_id): p.run_id for p in plans}
    return Directives(
        holds={(rid.get(r, r), s): t for (r, s), t in holds.items()},
        lines={(rid.get(r, r), s): n for (r, s), n in lines.items()},
        from_s=0.0,
        closures=list(closures),
        exit_windows=dict(exit_windows),
        entry_delays=dict(entry_delays or {}),
        incidents=list(incidents),
        terminations={rid.get(r, r): s for r, s in (terminations or {}).items()},
    )


class TwinFeed:
    """The ``obs.*`` feed of one twin day under a degradation profile, released by delivery time."""

    def __init__(self, world: run.World, day: dt.date, profile: str, until_s: float) -> None:
        self.world, self.day, self.profile, self.until_s = world, day, profile, until_s
        self.records: list[tuple[dt.datetime, str, bytes]] = []
        self.pos = 0
        self.delivered: dt.datetime | None = None
        self.simulations = 0

    def load(self, directives: Directives | None) -> None:
        """(Re)simulate the day under ``directives``; keep only what has not been delivered yet."""
        obs = Observer(self.world.register, self.profile, keep=True)
        run.run_day(self.world, self.day, until_s=self.until_s, observers=[obs], directives=directives)
        self.simulations += 1
        recs = sorted(
            ((e.ingest_time or e.event_time, topic, e.to_json()) for topic, e in obs.events),
            key=lambda r: r[0],
        )
        self.records = [r for r in recs if self.delivered is None or r[0] > self.delivered]
        self.pos = 0

    def pump(self, ingestor: Ingestor, now: dt.datetime) -> int:
        n = 0
        while self.pos < len(self.records) and self.records[self.pos][0] <= now:
            when, topic, raw = self.records[self.pos]
            ingestor.ingest(topic, raw)
            self.delivered = when
            self.pos += 1
            n += 1
        ingestor.tick(now)
        ingestor.flush()  # the DSS acts on everything delivered by now, not on the reorder buffer's lag
        self.delivered = now
        return n


class TwinDss:
    """DS-1, DS-3, DS-4 and DS-5 run incrementally on the feed."""

    def __init__(
        self,
        world: run.World,
        day: dt.date,
        *,
        profile: str = "D0",
        until_s: float,
        closures: Sequence[Closure] = (),
        exit_windows: Mapping[str, list[tuple[float, float]]] | None = None,
        degraded_predictor: bool = False,
        noise_seed: int = 0,
        incidents: Sequence[Any] = (),
        entry_delays: Mapping[str, float] | None = None,
    ) -> None:
        """``degraded_predictor`` is the BL-3 arm: the same chain with noisy ETAs (``pred.degraded``).
        ``incidents`` and ``entry_delays`` are the world's disruptions (ground truth, issue #155)."""
        reg = world.register
        self.world, self.day = world, day
        self.closures, self.exit_windows = list(closures), dict(exit_windows or {})
        self.incidents, self.entry_delays = list(incidents), dict(entry_delays or {})
        self.origin = day_start(day)
        self.directory = PlanDirectory.from_published(register=reg)
        self.estimator = StateEstimator(self.directory, reg)
        self.ingestor = Ingestor(reg, on_event=self.estimator.apply)
        process = ProcessTimes.from_log([], self.directory, reg)
        self.predictor: Predictor = (
            DegradedPredictor(process, reg, noise_seed) if degraded_predictor else Predictor(process, reg)
        )
        self.detector = Detector.build(self.directory, reg)
        self.feed = TwinFeed(world, day, profile, until_s)
        self.feed.load(self._directives({}, {}))
        self._alerts_seen = 0

    def _directives(
        self,
        holds: Mapping[tuple[str, str], float],
        lines: Mapping[tuple[str, str], int],
        terminations: Mapping[str, str] | None = None,
    ) -> Directives:
        return standing_directives(
            self.world,
            self.day,
            closures=self.closures,
            exit_windows=self.exit_windows,
            holds=holds,
            lines=lines,
            incidents=self.incidents,
            entry_delays=self.entry_delays,
            terminations=terminations,
        )

    def commit(
        self,
        holds: Mapping[tuple[str, str], float],
        lines: Mapping[tuple[str, str], int],
        terminations: Mapping[str, str] | None = None,
    ) -> None:
        self.feed.load(self._directives(holds, lines, terminations))

    def advance(self, now_s: float) -> DssView:
        now = self.origin + dt.timedelta(seconds=now_s)
        self.feed.pump(self.ingestor, now)
        snap = self.estimator.snapshot(now)
        preds = self.predictor.predict(self.estimator, now)
        occs = occupations(preds, snap, self.estimator, self.directory, self.predictor.horizon)
        conflicts = self.detector.detect(occs, now)
        fixes = {t.run_id: now_s - t.last_fix_age_s for t in snap.trains if t.last_fix_age_s is not None}
        new = self.ingestor.alerts[self._alerts_seen :]
        self._alerts_seen = len(self.ingestor.alerts)
        return DssView(
            now_s=now_s,
            version=snap.version,
            state_hash=snap.hash,
            alerts=[
                {"kind": a.kind, "priority": a.priority, "source": a.source, "detail": a.detail} for a in new
            ],
            snapshot=gates.StateSnapshot(
                snapshot_hash=f"v{snap.version}",
                network_version=snap.network_version,
                taken_at_s=now_s,
                train_last_fix_s=fixes,
            ),
            known_trains=frozenset(fixes),
            conflicts=list(conflicts),
        )


# --------------------------------------------------------------------------- the planner
def schedule_hint(instance: displib.Instance, doc: dict[str, Any], occupancy: Any) -> dict[str, Any]:
    """A twin schedule as a DISPLIB solution (times floored as in the IR): the CP-SAT warm start."""
    schedule = schedule_from_trace(instance, occupancy)
    paths: dict[int, list[tuple[int, int]]] = {}
    for ti, train in enumerate(instance.trains):
        preds = {e.operation for o in train.operations for e in o.successors}
        shift = int(len([o for o in train.operations if o.id not in preds]) > 1)
        ops = {o.id: o for o in train.operations}
        path = [(oid, math.floor(t + 1e-6)) for oid, t in schedule[ti]]
        p = [(0, path[0][1])] if shift else []
        p += [(shift + oid, t) for oid, t in path]
        last_oid, last_t = path[-1]
        p.append((shift + len(train.operations), last_t + ops[last_oid].min_duration))
        paths[ti] = p
    return {"objective_value": cpsat.objective_of(doc, paths), "events": cpsat.order_events(doc, paths)}


def decisions_of(
    instance: displib.Instance,
    solution: Mapping[str, Any],
    booked: Mapping[tuple[str, str], float | None],
    start_s: float,
    end_s: float,
) -> tuple[list[Decision], list[Hold]]:
    """The CP-SAT solution as departure decisions (station, line, time) and the holds beyond booked."""
    paths: dict[int, list[tuple[int, int]]] = {}
    for e in solution["events"]:
        paths.setdefault(int(e["train"]), []).append((int(e["operation"]), int(e["time"])))
    decisions: list[Decision] = []
    holds: list[Hold] = []
    for ti, path in paths.items():
        train = instance.trains[ti]
        preds = {e.operation for o in train.operations for e in o.successors}
        shift = int(len([o for o in train.operations if o.id not in preds]) > 1)
        run_id = token(train.id)
        for n, (idx, t) in enumerate(path[:-2]):  # the last station op is the exit, not a departure
            oid = idx - shift
            if not 0 <= oid < len(train.operations):
                continue
            op = train.operations[oid]
            nxt = path[n + 1][1]  # the IR floors its times; an order is never earlier than the plan: round up
            if op.kind != "station" or not start_s <= nxt < end_s:
                continue
            line_no = int(op.resources[0].resource.rpartition("/")[2])
            decisions.append(Decision(run_id, op.node, line_no, float(nxt)))
            base = booked.get((run_id, op.node))
            if op.min_duration > 0 and nxt - (base if base is not None else t) > MIN_HOLD_S:
                holds.append(
                    Hold(run_id, op.node, float(nxt), float(nxt) - (base if base is not None else t))
                )
    return decisions, holds


def decision_ops(instance: displib.Instance) -> dict[tuple[str, str, int], tuple[int, int]]:
    """``(run id, station, line) -> (train, operation)`` in the DISPLIB document: the inverse of
    :func:`decisions_of` (a decision is the end of its station operation)."""
    out: dict[tuple[str, str, int], tuple[int, int]] = {}
    for ti, train in enumerate(instance.trains):
        preds = {e.operation for o in train.operations for e in o.successors}
        shift = int(len([o for o in train.operations if o.id not in preds]) > 1)
        run_id = token(train.id)
        for oid, op in enumerate(train.operations):
            if op.kind != "station" or not op.resources:
                continue
            line_no = int(op.resources[0].resource.rpartition("/")[2])
            out.setdefault((run_id, op.node, line_no), (ti, oid + shift))
    return out


def _keeps(solution: Mapping[str, Any], fixed: Sequence[cpsat.Fixed]) -> bool:
    """Whether a DISPLIB solution uses every fixed operation and ends it within its bounds."""
    paths = cpsat.paths_from_solution({}, solution)
    ends = {(ti, a): t for ti, p in paths.items() for (a, _), (_, t) in itertools.pairwise(p)}
    return all((e := ends.get((ti, oi))) is not None and lo <= e <= hi for ti, oi, lo, hi in fixed)


def churn(previous: Sequence[Decision], current: Sequence[Decision], tolerance_s: float) -> int:
    """Decisions of the previous plan that the current one changes: another line, or a departure moved
    by more than ``tolerance_s`` (decisions only one of the plans covers are not counted)."""
    now = {(d.run_id, d.station): d for d in current}
    changed = 0
    for d in previous:
        c = now.get((d.run_id, d.station))
        if c is not None and (c.line_no != d.line_no or abs(c.dep_s - d.dep_s) > tolerance_s):
            changed += 1
    return changed


class TwinPlanner:
    """Builds and verifies the options of one cycle (greedy + warm-started CP-SAT).

    ``planner="cpsat"`` (issue #152) makes CP-SAT the primary planner: its verified plan carries the
    rolling state, with the frozen decisions and accepted orders as constraints, the previous plan kept
    by a stability term and the delay objective capped by the greedy hint's. Greedy is the fallback when
    CP-SAT has no verified plan in time."""

    def __init__(
        self,
        world: run.World,
        day: dt.date,
        *,
        cpsat_enabled: bool = True,
        planner: str = "greedy",
        alternatives: int = 1,
        workers: int | None = None,
        time_limit_s: float | None = None,
        register: Register | None = None,
        options: bool = True,
    ) -> None:
        """``options=False`` leaves out the disruption options and contingent plans (issue #156)."""
        self.world, self.day = world, day
        self.reg = register or world.register
        self.dispatcher = GreedyDispatcher(world, self.reg)
        self.planner = planner
        self.cpsat_enabled, self.alternatives = cpsat_enabled or planner == "cpsat", alternatives
        stab = self.reg.value("opt.stability")
        self.stability = (int(stab["per_s"]), int(stab["drop"]))
        self.churn_s = float(stab["churn_s"])
        self.freeze_tol = int(stab["freeze_tolerance_s"])
        self.time_limit_s = time_limit_s  # None: opt.time_limit_s of the cycle (routine or disruption)
        self.workers = workers if workers is not None else int(self.reg.value("opt.workers"))
        opts = self.reg.value("opt.disruption")  # disruption options (issue #156)
        self.options = options
        self.contingent_s = [float(m) * SECONDS_PER_MIN for m in opts["contingent_etr_min"]]
        self.short_terminate = bool(opts["short_terminate"])
        self.terminate_hold_s = float(opts["terminate_min_hold_min"]) * SECONDS_PER_MIN
        self.terminate_penalty = float(opts["terminate_penalty_min"])
        self.plans = {token(p.run_id): p for p in run.plans_for(world, day)[0]}

    def solver_info(self) -> dict[str, Any]:
        return {
            "planner": self.planner,
            "cpsat": self.cpsat_enabled,
            "alternatives": self.alternatives,
            "workers": self.workers,
            "time_limit_s": self.time_limit_s,
        }

    def rejudge(
        self,
        ctx: PlanContext,
        plan_id: str,
        decisions: list[Decision],
        holds: list[Hold],
        terminations: Mapping[str, str] | None = None,
    ) -> sandbox.G3Result:
        """G3 of a recorded plan: a solver option is replayed by checking its plan, not by solving again."""
        standing, baseline = self._world(ctx)
        return self._judge(ctx, plan_id, decisions, holds, standing, baseline, terminations)

    def _world(self, ctx: PlanContext) -> tuple[Directives, Any]:
        """What holds in the world the plan is judged in, and the BL-0 run in it (the baseline)."""
        standing = standing_directives(
            self.world,
            self.day,
            closures=ctx.closures,
            exit_windows=ctx.exit_windows,
            holds=ctx.commit_holds,
            lines=ctx.commit_lines,
            incidents=ctx.incidents,
            entry_delays=ctx.entry_delays,
            terminations=ctx.commit_terminations,
        )
        plans, _, inst = run.plans_for(self.world, self.day)
        baseline = run.run_day(
            self.world,
            self.day,
            plans=plans,
            until_s=ctx.now_s + self.dispatcher.horizon_s,
            keep_events=True,
            directives=standing,
            scenario_id=f"{inst}-loop-baseline",
        )
        return standing, baseline

    def _replan(
        self,
        ctx: PlanContext,
        imposed: Sequence[Decision],
        checked: Sequence[Decision],
        suffix: str = "",
        *,
        horizon_s: float | None = None,
        terminations: Mapping[str, str] | None = None,
        promote: Sequence[str] = (),
    ) -> GreedyPlan:
        """The greedy plan of the cycle; an option adds its short-terminations or promoted trains."""
        return self.dispatcher.plan(
            self.day,
            ctx.now_s,
            horizon_s=horizon_s,
            frozen=imposed,
            committed=checked,
            closures=ctx.closures,
            exit_windows=ctx.exit_windows,
            plan_id=ctx.plan_id + suffix,
            policy_from_s=ctx.policy_from_s,
            incidents=ctx.incidents,
            entry_delays=ctx.entry_delays,
            terminations={**ctx.commit_terminations, **(terminations or {})},
            promote=promote,
        )

    def plan(self, ctx: PlanContext) -> PlanSet:
        t0 = time.perf_counter()
        imposed, checked = self._frozen_of(ctx, impose=self.planner == "cpsat")
        gp = self._replan(ctx, imposed, checked)
        out = PlanSet(decisions=gp.decisions)
        if not ctx.verify:
            out.runtime_s = round(time.perf_counter() - t0, 2)
            return out
        end_s = ctx.now_s + self.dispatcher.horizon_s
        standing, baseline = self._world(ctx)
        out.baseline = sandbox.kpis(baseline[1], self.day, ctx.now_s, end_s)
        seen: set[tuple[Any, ...]] = set()

        def add(cand: Candidate, force: bool = False) -> None:
            sig = self._signature(cand)
            if sig in seen and not force:
                return
            seen.add(sig)
            out.candidates.append(cand)
            if cand.verified and out.first_plan_s is None:  # DSC-06: the first verified option
                out.first_plan_s = round(time.perf_counter() - t0, 2)

        add(self._greedy(ctx, gp, standing, baseline))
        if self.options and self._disrupted(ctx, end_s):
            for option in self._options(ctx, gp, standing, baseline, imposed, checked):
                add(option)
            if "disruption" in ctx.triggers:
                out.contingent = self._contingent(ctx, imposed, checked)
        if self.cpsat_enabled:
            # the greedy plan to the end of the day: a complete schedule to warm-start the solver with
            full = self._replan(ctx, imposed, checked, "-hint", horizon_s=HINT_END_S - ctx.now_s)
            for k in range(self.alternatives + 1):
                cand = self._cpsat(ctx, gp, full, standing, baseline, seed=k)
                if cand is None:
                    out.notes.append(f"cpsat seed {k}: no solution")
                    continue
                # the primary plan is kept even when it equals greedy's
                add(cand, force=k == 0 and self.planner == "cpsat")
        if self.planner == "cpsat":
            primary = next((c for c in out.candidates if c.name == "cpsat" and c.verified), None)
            if primary is not None:
                out.decisions = [
                    Decision(d.run_id, d.station, d.line_no, d.dep_s) for d in primary.g3.departures
                ]
                out.planner = "cpsat"
            else:
                out.notes.append("planner: no verified CP-SAT plan, greedy fallback")
        out.churn = churn(ctx.previous, out.decisions, self.churn_s) if ctx.previous else None
        out.runtime_s = round(time.perf_counter() - t0, 2)
        return out

    # ------------------------------------------------------------------ candidates
    @staticmethod
    def _frozen_of(ctx: PlanContext, impose: bool = False) -> tuple[list[Decision], list[Decision]]:
        """What the greedy plan must keep: (imposed as directives, only checked by G2).

        The plan is a pure function of the day and its directives, so an unchanged world reproduces its
        own earlier decisions; a hold directive on a train that was queueing would send it to the back of
        the queue. Decisions are imposed only when the world changed (closures, closed exits), and checked
        always (VER-FRZ-01, issue #127). With CP-SAT as the primary planner (issue #152) the frozen decisions
        come from another planner's plan, so greedy (the fallback and the hint) imposes them too."""
        fixed = ctx.frozen + ctx.history
        return (fixed, ctx.committed) if ctx.impose or impose else ([], fixed + ctx.committed)

    @staticmethod
    def _signature(c: Candidate) -> tuple[Any, ...]:
        return (
            tuple(sorted((h.run_id, h.station, round(h.until_s)) for h in c.holds)),
            tuple(sorted((d.run_id, d.station, d.line_no) for d in c.decisions)),
            tuple(sorted(c.terminations.items())),
        )

    def _sandbox(
        self,
        ctx: PlanContext,
        plan_id: str,
        decisions: list[Decision],
        holds: list[Hold],
        terminations: Mapping[str, str] | None = None,
    ) -> sandbox.SandboxPlan:
        frozen = ctx.frozen + ctx.history
        keys = {(f.run_id, f.station) for f in frozen}
        return sandbox.SandboxPlan(
            plan_id=plan_id,
            holds=[
                sandbox.Hold(run_id=h.run_id, station=h.station, until_s=h.until_s)
                for h in holds
                if (h.run_id, h.station) not in keys
            ]
            + [sandbox.Hold(run_id=f.run_id, station=f.station, until_s=f.dep_s) for f in frozen],
            lines=[
                sandbox.LineChoice(run_id=d.run_id, station=d.station, line_no=d.line_no)
                for d in decisions
                if (d.run_id, d.station) not in keys
            ]
            + [sandbox.LineChoice(run_id=f.run_id, station=f.station, line_no=f.line_no) for f in frozen],
            maintenance=[
                MaintenanceBlock(
                    block_id=self.dispatcher.block_of[c.segment],
                    line_id=c.line_id,
                    start_s=c.start_s,
                    end_s=c.end_s,
                )
                for c in ctx.closures
            ],
            frozen=[
                FrozenCall(run_id=f.run_id, op=f.station, line_no=f.line_no, dep_s=f.dep_s) for f in frozen
            ],
            terminations=dict(terminations or {}),
        )

    def _judge(
        self,
        ctx: PlanContext,
        plan_id: str,
        decisions: list[Decision],
        holds: list[Hold],
        standing: Directives,
        baseline: Any,
        terminations: Mapping[str, str] | None = None,
    ) -> sandbox.G3Result:
        return sandbox.replay(
            self.world,
            self.day,
            ctx.now_s,
            self._sandbox(ctx, plan_id, decisions, holds, terminations),
            register=self.reg,
            net=self.dispatcher.net,
            standing=standing,
            baseline=baseline,
        )

    def _greedy(
        self,
        ctx: PlanContext,
        gp: GreedyPlan,
        standing: Directives,
        baseline: Any,
        name: str = "greedy",
        terminations: Mapping[str, str] | None = None,
    ) -> Candidate:
        ends = dict(terminations or {})
        g3 = self._judge(ctx, f"{ctx.plan_id}-{name}", gp.decisions, gp.holds, standing, baseline, ends)
        return Candidate(
            name=name,
            plan_id=ctx.plan_id,
            decisions=[d for d in gp.decisions if d.run_id not in ends],
            holds=[h for h in gp.holds if h.run_id not in ends],
            g1=None,
            g2=gp.g2,
            g3=g3,
            solver={"g2": gp.g2.overall.value, "stuck": len(gp.stuck)},
            terminations=ends,
        )

    # ------------------------------------------------------------------ disruption options (issue #156)
    @staticmethod
    def _disrupted(ctx: PlanContext, end_s: float) -> bool:
        """An incident or a closure is in force in the plan's horizon."""
        return any(c.start_s < end_s and c.end_s > ctx.now_s for c in ctx.closures) or any(
            d.start_s < end_s and d.end_s > ctx.now_s for d in ctx.incidents
        )

    def _options(
        self,
        ctx: PlanContext,
        gp: GreedyPlan,
        standing: Directives,
        baseline: Any,
        imposed: Sequence[Decision],
        checked: Sequence[Decision],
    ) -> list[Candidate]:
        """FR-OPT-11: re-order / re-cross, and (policy-gated) short-terminate or cancel."""
        out: list[Candidate] = []
        held = sorted(gp.holds, key=lambda h: (-h.held_s, h.run_id, h.station))
        if held:  # the train held longest goes first: the others wait for it and cross it elsewhere
            top = held[0]
            alt = self._replan(ctx, imposed, checked, "-recross", promote=[top.run_id])
            cand = self._greedy(ctx, alt, standing, baseline, name="recross")
            cand.solver |= {"promoted": top.run_id, "held_min": round(top.held_s / SECONDS_PER_MIN, 1)}
            out.append(cand)
        pick = self._terminable(ctx, held) if self.short_terminate else None
        if pick is not None:
            rid, station, cancel = pick
            alt = self._replan(ctx, imposed, checked, "-term", terminations={rid: station})
            name = "cancel" if cancel else "short-term"
            cand = self._greedy(ctx, alt, standing, baseline, name=name, terminations={rid: station})
            cand.cancelled = frozenset({rid}) if cancel else frozenset()
            cand.penalty_min = self.terminate_penalty
            out.append(cand)
        return out

    def _terminable(self, ctx: PlanContext, held: Sequence[Hold]) -> tuple[str, str, bool] | None:
        """The passenger train held longest (at least ``terminate_min_hold_min``) at a station it stops at:
        (run id, station, cancelled); a cancellation when that is its first station and it has not entered."""
        for h in held:
            p = self.plans.get(h.run_id)
            if h.held_s < self.terminate_hold_s or p is None or p.kind != "passenger" or not p.timetabled:
                continue
            if h.run_id in ctx.commit_terminations:
                continue
            if p.nodes[0].op == h.station:
                if p.entry_s >= ctx.now_s:
                    return h.run_id, h.station, True
            elif any(n.op == h.station and n.stop for n in p.nodes[1:-1]):
                return h.run_id, h.station, False
        return None

    def _contingent(
        self, ctx: PlanContext, imposed: Sequence[Decision], checked: Sequence[Decision]
    ) -> list[Contingent]:
        """Plans for the incidents' ETR + each ``contingent_etr_min``, each verified in its own scenario (the
        incidents last that much longer); the cycle's own plan is the one for the announced ETR."""
        uncertain = {d.incident_id for d in ctx.incidents if d.etr_s is not None and d.etr_s > ctx.now_s}
        if not uncertain:
            return []
        etr = max(d.etr_s for d in ctx.incidents if d.incident_id in uncertain)
        out: list[Contingent] = []
        switch = etr
        for extra in self.contingent_s:
            longer = [
                d.model_copy(update={"duration_s": d.duration_s + extra, "etr_s": d.etr_s + extra})
                if d.incident_id in uncertain
                else d
                for d in ctx.incidents
            ]
            name = f"contingent-etr+{round(extra / SECONDS_PER_MIN)}"
            sub = replace(ctx, incidents=longer, plan_id=f"{ctx.plan_id}-{name}")
            standing, baseline = self._world(sub)
            cand = self._greedy(sub, self._replan(sub, imposed, checked), standing, baseline, name=name)
            out.append(Contingent(etr_s=etr + extra, switch_at_s=switch, candidate=cand))
            switch = etr + extra
        return out

    def _cpsat(
        self,
        ctx: PlanContext,
        gp: GreedyPlan,
        full: GreedyPlan,
        standing: Directives,
        baseline: Any,
        *,
        seed: int,
    ) -> Candidate | None:
        end_s = ctx.now_s + self.dispatcher.horizon_s
        snap = displib.snapshot_from_occupancy(full.occupancy, ctx.now_s, self.world.layout.network_version)
        instance, _ = displib.build_instance(
            self.world,
            self.day,
            ctx.now_s,
            end_s,
            snapshot=snap,
            incidents=ctx.incidents,
            entry_delays=ctx.entry_delays,
        )
        doc = instance.to_displib()
        hint: dict[str, Any] | None = None
        try:
            hint = schedule_hint(instance, doc, full.occupancy)
            if not gates.g1(doc, hint, register=self.reg).passed:
                hint = None
        except Exception:  # a hint that cannot be built is no reason to skip the solve
            hint = None
        fixed, preferred, missing = self._rolling(ctx, instance, end_s)
        rolling = self.planner == "cpsat"  # every CP-SAT option keeps the frozen decisions
        primary = rolling and seed == 0  # the primary plan also keeps the previous one (stability)
        if not rolling:
            fixed, missing = [], 0
        # the hint bounds the delay objective only when it is a schedule of the constrained model
        capped = primary and hint is not None and _keeps(hint, fixed)
        result = cpsat.solve(
            doc,
            time_limit_s=self.time_limit_s or ctx.time_limit_s,
            workers=self.workers,
            hint=hint,
            seed=seed,
            fixed=fixed,
            preferred=preferred if primary else (),
            stability=self.stability if primary else (0, 0),
            delay_cap=hint["objective_value"] if capped and hint else None,
        )
        if result.solution is None:
            return None
        names = [t.id for t in instance.trains]
        g1 = gates.g1(doc, result.solution, names=names, register=self.reg)
        booked = {
            (t.run_id, c.op): c.booked_dep_s
            for t in gp.plan.trains
            for c in t.calls
            if c.booked_dep_s is not None
        }
        decisions, holds = decisions_of(instance, result.solution, booked, ctx.now_s, end_s)
        name = "cpsat" if seed == 0 else f"cpsat-alt{seed}"
        g3 = self._judge(ctx, f"{ctx.plan_id}-{name}", decisions, holds, standing, baseline)
        return Candidate(
            name=name,
            plan_id=ctx.plan_id,
            decisions=decisions,
            holds=holds,
            g1=g1,
            g2=None,
            g3=g3,
            solver={
                "status": result.status,
                "objective": result.objective,
                "bound": result.bound,
                "wall_s": result.wall_s,
                "warm_started": hint is not None,
                "hint_objective": hint["objective_value"] if hint else None,
                "trains": result.size.get("trains"),
                "primary": primary,
                "capped": capped,
                "fixed": len(fixed),
                "fixed_outside_ir": missing,
                "stability": result.stability,
            },
        )

    def _rolling(
        self, ctx: PlanContext, instance: displib.Instance, end_s: float
    ) -> tuple[list[cpsat.Fixed], list[cpsat.Preferred], int]:
        """The rolling-horizon terms of the primary solve: frozen decisions and accepted orders are fixed
        (their end within ``opt.stability.freeze_tolerance_s`` of the decision), the previous plan's other
        decisions in the window are preferred. Returns how many frozen decisions the IR does not model."""
        ops = decision_ops(instance)
        fixed: list[cpsat.Fixed] = []
        keys: set[tuple[str, str]] = set()
        missing = 0
        for d in [*ctx.frozen, *ctx.committed]:
            if (d.run_id, d.station) in keys or not ctx.now_s <= d.dep_s < end_s:
                continue
            at = ops.get((d.run_id, d.station, d.line_no))
            if at is None:
                missing += 1
                continue
            keys.add((d.run_id, d.station))
            tol = self.freeze_tol
            fixed.append((at[0], at[1], math.floor(d.dep_s) - tol, math.ceil(d.dep_s) + tol))
        preferred: list[cpsat.Preferred] = []
        for d in ctx.previous:
            if (d.run_id, d.station) in keys or not ctx.now_s <= d.dep_s < end_s:
                continue
            at = ops.get((d.run_id, d.station, d.line_no))
            if at is not None:
                preferred.append((at[0], at[1], round(d.dep_s)))
        return fixed, preferred, missing


# --------------------------------------------------------------------------- the day's KPIs
def day_kpis(
    world: run.World,
    day: dt.date,
    *,
    closures: Sequence[Closure],
    exit_windows: Mapping[str, list[tuple[float, float]]],
    holds: Mapping[tuple[str, str], float],
    lines: Mapping[tuple[str, str], int],
    incidents: Sequence[Any] = (),
    entry_delays: Mapping[str, float] | None = None,
    arm: str = "DSS",
    events_out: list[Any] | None = None,
    terminations: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """FR-KPI-05: the day under the loop's orders (the ``arm``) against BL-0 with the same disruptions.

    ``events_out`` receives the ground-truth events of that arm (for D2, issue #162)."""
    from raildss_dss.kpi import compare_runs

    scope = f"day {day.isoformat()}"
    same = {
        "closures": closures,
        "exit_windows": exit_windows,
        "incidents": incidents,
        "entry_delays": entry_delays,
    }
    base = standing_directives(world, day, holds={}, lines={}, **same)  # type: ignore[arg-type]
    dss = standing_directives(
        world,
        day,
        holds=holds,
        lines=lines,
        terminations=terminations,
        **same,  # type: ignore[arg-type]
    )
    return compare_runs(
        run_arm(world, day, arm, scope, directives=dss, events_out=events_out),
        [run_arm(world, day, "BL-0", scope, directives=base)],
    )
