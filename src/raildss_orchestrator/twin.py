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
"""

from __future__ import annotations

import datetime as dt
import math
import time
from collections.abc import Mapping, Sequence
from typing import Any

from raildss_dss.conflicts import Detector, occupations
from raildss_dss.ingest import Ingestor
from raildss_dss.predict import Predictor, ProcessTimes
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

from .model import Candidate, DssView, PlanContext, PlanSet

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
) -> Directives:
    """What holds in the world: closures, closed exits and the accepted orders (BL-0 does the rest)."""
    plans, _, _ = run.plans_for(world, day)
    rid = {token(p.run_id): p.run_id for p in plans}
    return Directives(
        holds={(rid.get(r, r), s): t for (r, s), t in holds.items()},
        lines={(rid.get(r, r), s): n for (r, s), n in lines.items()},
        from_s=0.0,
        closures=list(closures),
        exit_windows=dict(exit_windows),
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
    ) -> None:
        reg = world.register
        self.world, self.day = world, day
        self.closures, self.exit_windows = list(closures), dict(exit_windows or {})
        self.origin = day_start(day)
        self.directory = PlanDirectory.from_published(register=reg)
        self.estimator = StateEstimator(self.directory, reg)
        self.ingestor = Ingestor(reg, on_event=self.estimator.apply)
        self.predictor = Predictor(ProcessTimes.from_log([], self.directory, reg), reg)
        self.detector = Detector.build(self.directory, reg)
        self.feed = TwinFeed(world, day, profile, until_s)
        self.feed.load(self._directives({}, {}))

    def _directives(
        self, holds: Mapping[tuple[str, str], float], lines: Mapping[tuple[str, str], int]
    ) -> Directives:
        return standing_directives(
            self.world,
            self.day,
            closures=self.closures,
            exit_windows=self.exit_windows,
            holds=holds,
            lines=lines,
        )

    def commit(self, holds: Mapping[tuple[str, str], float], lines: Mapping[tuple[str, str], int]) -> None:
        self.feed.load(self._directives(holds, lines))

    def advance(self, now_s: float) -> DssView:
        now = self.origin + dt.timedelta(seconds=now_s)
        self.feed.pump(self.ingestor, now)
        snap = self.estimator.snapshot(now)
        preds = self.predictor.predict(self.estimator, now)
        occs = occupations(preds, snap, self.estimator, self.directory, self.predictor.horizon)
        conflicts = self.detector.detect(occs, now)
        fixes = {t.run_id: now_s - t.last_fix_age_s for t in snap.trains if t.last_fix_age_s is not None}
        return DssView(
            now_s=now_s,
            version=snap.version,
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


class TwinPlanner:
    """Builds and verifies the options of one cycle (greedy + warm-started CP-SAT)."""

    def __init__(
        self,
        world: run.World,
        day: dt.date,
        *,
        cpsat_enabled: bool = True,
        alternatives: int = 1,
        workers: int | None = None,
        time_limit_s: float | None = None,
        register: Register | None = None,
    ) -> None:
        self.world, self.day = world, day
        self.reg = register or world.register
        self.dispatcher = GreedyDispatcher(world, self.reg)
        self.cpsat_enabled, self.alternatives = cpsat_enabled, alternatives
        self.time_limit_s = time_limit_s  # None: opt.time_limit_s of the cycle (routine or disruption)
        self.workers = workers if workers is not None else int(self.reg.value("opt.workers"))

    def plan(self, ctx: PlanContext) -> PlanSet:
        t0 = time.perf_counter()
        imposed, checked = self._frozen_of(ctx)
        gp = self.dispatcher.plan(
            self.day,
            ctx.now_s,
            frozen=imposed,
            committed=checked,
            closures=ctx.closures,
            exit_windows=ctx.exit_windows,
            plan_id=ctx.plan_id,
            policy_from_s=ctx.policy_from_s,
        )
        out = PlanSet(decisions=gp.decisions)
        if not ctx.verify:
            out.runtime_s = round(time.perf_counter() - t0, 2)
            return out
        end_s = ctx.now_s + self.dispatcher.horizon_s
        standing = standing_directives(
            self.world,
            self.day,
            closures=ctx.closures,
            exit_windows=ctx.exit_windows,
            holds=ctx.commit_holds,
            lines=ctx.commit_lines,
        )
        plans, _, inst = run.plans_for(self.world, self.day)
        baseline = run.run_day(
            self.world,
            self.day,
            plans=plans,
            until_s=end_s,
            keep_events=True,
            directives=standing,
            scenario_id=f"{inst}-loop-baseline",
        )
        out.baseline = sandbox.kpis(baseline[1], self.day, ctx.now_s, end_s)
        out.candidates.append(self._greedy(ctx, gp, standing, baseline))
        if self.cpsat_enabled:
            seen = {self._signature(out.candidates[0])}
            # the greedy plan to the end of the day: a complete schedule to warm-start the solver with
            full = self.dispatcher.plan(
                self.day,
                ctx.now_s,
                horizon_s=HINT_END_S - ctx.now_s,
                frozen=imposed,
                committed=checked,
                closures=ctx.closures,
                exit_windows=ctx.exit_windows,
                plan_id=f"{ctx.plan_id}-hint",
                policy_from_s=ctx.policy_from_s,
            )
            for k in range(self.alternatives + 1):
                cand = self._cpsat(ctx, gp, full, standing, baseline, seed=k)
                if cand is None:
                    out.notes.append(f"cpsat seed {k}: no solution")
                    continue
                sig = self._signature(cand)
                if sig not in seen:
                    seen.add(sig)
                    out.candidates.append(cand)
        out.runtime_s = round(time.perf_counter() - t0, 2)
        return out

    # ------------------------------------------------------------------ candidates
    @staticmethod
    def _frozen_of(ctx: PlanContext) -> tuple[list[Decision], list[Decision]]:
        """What the greedy plan must keep: (imposed as directives, only checked by G2).

        The plan is a pure function of the day and its directives, so an unchanged world reproduces its
        own earlier decisions; a hold directive on a train that was queueing would send it to the back of
        the queue. Decisions are imposed only when the world changed (closures, closed exits), and checked
        always (VER-FRZ-01, issue #127)."""
        fixed = ctx.frozen + ctx.history
        return (fixed, ctx.committed) if ctx.impose else ([], fixed + ctx.committed)

    @staticmethod
    def _signature(c: Candidate) -> tuple[Any, ...]:
        return tuple(sorted((h.run_id, h.station, round(h.until_s)) for h in c.holds)), tuple(
            sorted((d.run_id, d.station, d.line_no) for d in c.decisions)
        )

    def _sandbox(
        self, ctx: PlanContext, plan_id: str, decisions: list[Decision], holds: list[Hold]
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
        )

    def _judge(
        self,
        ctx: PlanContext,
        plan_id: str,
        decisions: list[Decision],
        holds: list[Hold],
        standing: Directives,
        baseline: Any,
    ) -> sandbox.G3Result:
        return sandbox.replay(
            self.world,
            self.day,
            ctx.now_s,
            self._sandbox(ctx, plan_id, decisions, holds),
            register=self.reg,
            net=self.dispatcher.net,
            standing=standing,
            baseline=baseline,
        )

    def _greedy(self, ctx: PlanContext, gp: GreedyPlan, standing: Directives, baseline: Any) -> Candidate:
        g3 = self._judge(ctx, f"{ctx.plan_id}-greedy", gp.decisions, gp.holds, standing, baseline)
        return Candidate(
            name="greedy",
            plan_id=ctx.plan_id,
            decisions=gp.decisions,
            holds=gp.holds,
            g1=None,
            g2=gp.g2,
            g3=g3,
            solver={"g2": gp.g2.overall.value, "stuck": len(gp.stuck)},
        )

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
        instance, _ = displib.build_instance(self.world, self.day, ctx.now_s, end_s, snapshot=snap)
        doc = instance.to_displib()
        hint: dict[str, Any] | None = None
        try:
            hint = schedule_hint(instance, doc, full.occupancy)
            if not gates.g1(doc, hint, register=self.reg).passed:
                hint = None
        except Exception:  # a hint that cannot be built is no reason to skip the solve
            hint = None
        result = cpsat.solve(
            doc,
            time_limit_s=self.time_limit_s or ctx.time_limit_s,
            workers=self.workers,
            hint=hint,
            seed=seed,
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
            },
        )


# --------------------------------------------------------------------------- the day's KPIs
def day_kpis(
    world: run.World,
    day: dt.date,
    *,
    closures: Sequence[Closure],
    exit_windows: Mapping[str, list[tuple[float, float]]],
    holds: Mapping[tuple[str, str], float],
    lines: Mapping[tuple[str, str], int],
) -> dict[str, Any]:
    """FR-KPI-05: the day under the loop's orders (DSS arm) against BL-0 with the same disruptions."""
    from raildss_dss.kpi import compare_runs

    scope = f"day {day.isoformat()}"
    base = standing_directives(world, day, closures=closures, exit_windows=exit_windows, holds={}, lines={})
    dss = standing_directives(
        world, day, closures=closures, exit_windows=exit_windows, holds=holds, lines=lines
    )
    return compare_runs(
        run_arm(world, day, "DSS", scope, directives=dss),
        [run_arm(world, day, "BL-0", scope, directives=base)],
    )
