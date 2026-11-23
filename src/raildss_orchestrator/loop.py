"""The rolling decision loop (spec DS-9, FR-OPT-07, FR-VER-07; OD-25; issue #84).

On every tick of a (simulated) clock the loop advances the DSS chain (ingestion, state, prediction,
conflict detection) and looks for trigger events. When a re-plan is due (a trigger, or
``opt.replan_max_interval_min`` since the last plan) it plans, verifies and releases:

1. **Plan.** The planner builds the greedy plan and, when there is something to release, the warm-started
   CP-SAT options. Decisions inside the freeze window and orders already issued are frozen.
2. **Verify.** G0 (state sanity) before planning and again before execution, G1 (solver feasibility), G2
   (rule catalogue) and G3 (sandbox replay). Only an option with no FAIL is released (FR-VER-07).
3. **Release.** For each detected conflict at most :data:`~raildss_orchestrator.model.MAX_OPTIONS`
   verified options, ranked by KPI delta against the baseline (BL-0), enter the HITL lifecycle as PRESENTED
   recommendations; the baseline travels with them as the reference. A newer option for the same decision
   supersedes the open one; one not acted on by ``valid_until`` expires (FR-HITL-07).
4. **Execute.** A controller accepts; in headless evaluation runs the best option is accepted automatically
   (``auto_accept``, evaluation mode only). The executor re-verifies (G0 before execution), issues the control
   orders and the station masters acknowledge or decline them in time (``hitl.sm_ack_latency_s``).

Triggers (``opt.replan_triggers``): ``new_conflict``, ``disruption`` (a closure is announced),
``controller_action`` (an option is accepted or rejected), ``order_decline``.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Hashable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

from raildss_audit.chain import pseudonym
from raildss_dss.alert_kpi import AlertLog
from raildss_dss.conflict_kpi import ConflictLog
from raildss_hitl.executor import Executor, Option, PlanAction, ReplanRecord, Verdict
from raildss_hitl.lifecycle import COMMITTED, Lifecycle, Status
from raildss_optimiser.greedy import Decision
from raildss_params import Register
from raildss_twin.sim import Closure
from raildss_verifier import gates

from .auditing import EVALUATION_POLICY, AuditRecorder
from .cadence import Cadence
from .model import (
    KPI_RANK,
    MAX_OPTIONS,
    Candidate,
    Contrast,
    Dss,
    DssView,
    PlanContext,
    Planner,
    PlanSet,
    order_of,
)

SECONDS_PER_MIN = 60
OPEN_CONFLICT_PRIORITIES = ("P1", "P2", "P3")  # P4 is informational
END_KINDS = ("short_terminate", "cancel")  # orders that end a train's run (issue #156)


@dataclass(frozen=True)
class Disruption:
    """A closure the DSS learns about at ``announced_s`` (it starts at ``closure.start_s``)."""

    closure: Closure
    announced_s: float


class OptionRecord(BaseModel):
    rec_id: str
    context: str
    rank: int
    candidate: str
    actions: list[str]
    kpi_delta: dict[str, float]
    status: str
    warn: list[str] = Field(default_factory=list)


class CandidateRecord(BaseModel):
    name: str
    verified: bool
    fail: list[str]
    warn: list[str]
    g3: str
    kpi_delta: dict[str, float]
    solver: dict[str, Any] = Field(default_factory=dict)


class ContingentRecord(BaseModel):
    """A contingent plan for one ETR scenario and its switch rule (FR-OPT-11, issue #156)."""

    name: str
    etr_s: float
    switch_at_s: float
    verified: bool
    fail: list[str]
    kpi_delta: dict[str, float]
    rule: str


class DecisionRecord(BaseModel):
    """The options released for one decision (issue #154): how many, and how many different trains the
    verified plans that give it orders serve first at its resource (precedences; issue #196: the orders
    already in force count, as they are the standing alternative)."""

    trains: list[str]
    resource: str
    options: int
    precedences: int


class ContrastRecord(BaseModel):
    """'Why not X' for a decision of the cycle (issue #154): the other precedence, re-solved and verified."""

    first: str
    resource: str
    verified: bool
    reversed: bool
    cost_min: float | None
    fail: list[str]
    answer: str


class CycleRecord(BaseModel):
    cycle: int
    now_s: float
    triggers: list[str]
    plan_id: str
    conflicts: int
    blocked_by_g0: int
    candidates: list[CandidateRecord]
    baseline: dict[str, float]
    released: list[OptionRecord]
    notes: list[str] = Field(default_factory=list)
    expired: list[str] = Field(default_factory=list)
    wall_s: float
    planner: str = "greedy"  # the plan that carried the rolling state (issue #152)
    churn: int | None = None  # decisions of the previous plan this one changed
    first_plan_s: float | None = None  # wall time to the first verified option (DSC-06, issue #156)
    decisions: list[DecisionRecord] = Field(default_factory=list)  # options per decision (issue #154)
    contrasts: list[ContrastRecord] = Field(default_factory=list)  # the other precedence (issue #154)
    contingent: list[ContingentRecord] = Field(default_factory=list)  # ETR scenarios (issue #156)


class RunSummary(BaseModel):
    from_s: float
    to_s: float
    ticks: int
    cycles: list[CycleRecord]
    replan_starts_s: list[float]
    max_replan_gap_s: float
    released: int
    accepted: int
    expired: int
    superseded: int
    orders: int
    declined: int
    alerts: list[str]
    replans: list[dict[str, Any]]
    statuses: dict[str, int]
    max_options_per_decision: int


@dataclass
class _Entry:
    """What an option was built on, for the pre-execution check and the order bookkeeping."""

    candidate: Candidate
    trains: tuple[str, ...]
    version: int
    closures: list[str]
    network_version: str


@dataclass
class Orchestrator:
    register: Register
    dss: Dss
    planner: Planner
    disruptions: tuple[Disruption, ...] = ()
    exit_windows: Mapping[str, list[tuple[float, float]]] = field(default_factory=dict)
    auto_accept: bool = True  # headless evaluation: the controller accepts the best option
    decline: Callable[[dict[str, Any]], bool] | None = None  # a station master declines this order
    max_conflicts: int = 6  # conflicts released per cycle, most urgent first
    audit: AuditRecorder | None = None  # the tamper-evident trail (FR-AUD-01)
    # incidents the plans model once announced (injector disruptions: TSR, loop out of use, signal failure)
    # and known late entries (run id -> seconds late), issue #155
    incidents: tuple[tuple[Any, float], ...] = ()
    entry_delays: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cadence = Cadence(self.register)
        self.lc = Lifecycle()
        self.now_s = 0.0
        ack = self.register.value("hitl.sm_ack_latency_s")
        self.ack_latency_s = (float(ack["min"]) + float(ack["max"])) / 2
        self.executor = Executor(
            self.lc,
            self._publish,
            self._verify,
            self._replan,
            lambda: self.now_s,
            validity_s=self.cadence.max_interval_s,
        )
        self.cycles: list[CycleRecord] = []
        origin = getattr(self.dss, "origin", None)  # the DS-5 log needs the scenario clock
        self.conflict_log = ConflictLog(origin) if origin is not None else None
        self.alert_log = AlertLog(self.register)  # H1, H2 (DSC-09)
        self.published: list[tuple[str, dict[str, Any]]] = []
        self._entries: dict[str, _Entry] = {}  # option id -> what it was built on
        self._alternatives: dict[str, list[Option]] = {}  # rec id -> verified options of the same decision
        self._decisions: dict[str, Decision] = {}  # order id -> the decision it fixes
        self._acks: list[tuple[float, str]] = []
        self._events: set[str] = set()
        self._seen: set[tuple[Any, ...]] = set()
        self._announced: set[int] = set()
        self._announced_incidents: set[int] = set()
        self._revised_incidents: set[int] = set()  # cleared or past their announced ETR (issue #156)
        self._reopen = False  # an incident became known or changed: re-open the commit window (#156)
        self._withdrawn: set[str] = set()  # order ids withdrawn by a re-opened commit window
        self._ends: dict[str, tuple[str, str]] = {}  # order id -> (run id, station): short-term., cancel
        self._last_plan: list[Decision] = []
        self._view: DssView | None = None
        self._version: int | None = None
        self._ids = 0
        self._cycle_expired: list[str] = []
        self.ticks = 0
        self.max_options = 0
        self._run_id = ""
        self._presented_s: dict[str, float] = {}
        self._executor_alerts = 0
        self._rec_trains: dict[str, tuple[str, ...]] = {}
        self._audited: set[tuple[Any, ...]] = set()
        self._last_ctx: PlanContext | None = None
        # until then the plan in force holds orders of an option the planner does not reproduce (a contrasting
        # option, issue #196): its frozen decisions are imposed
        self._foreign_until_s = -math.inf
        self._foreign: set[str] = set()  # order ids of such options
        self._decision_records: list[DecisionRecord] = []

    # ------------------------------------------------------------------ the loop
    def run(self, from_s: float, to_s: float, tick_s: float = 60.0) -> RunSummary:
        t = from_s
        while t <= to_s:
            self.tick(t)
            t += tick_s
        return self.summary(from_s, to_s)

    def tick(self, now_s: float) -> CycleRecord | None:
        self.now_s = now_s
        self.ticks += 1
        self.lc.advance(now_s)
        self._answer_acks()
        view = self.dss.advance(now_s)
        self._view = view
        if self.conflict_log is not None:
            self.conflict_log.record(now_s, view.conflicts)
        self.alert_log.record(now_s, view.conflicts, view.alerts)
        self._audit_view(view)
        if self._version is not None and view.version != self._version:
            self.executor.state_changed()  # a material change: plans in force diverge
        self._version = view.version
        self._cycle_expired += [str(r) for r in self.lc.expire_due()]
        events = self._detect_triggers(view)
        if not self.cadence.due(now_s, events):
            self._audit_sweep()
            return None
        handled = set(events)  # what this cycle answers; events its own actions raise wait for the next one
        record = self._cycle(view, self.cadence.fired(events))
        self._events -= handled
        self._audit_sweep()
        return record

    # ------------------------------------------------------------------ triggers
    def _detect_triggers(self, view: DssView) -> set[str]:
        events = set(self._events)
        for c in view.conflicts:
            if c.priority in OPEN_CONFLICT_PRIORITIES and c.key not in self._seen:
                self._seen.add(c.key)
                events.add("new_conflict")
        for i, d in enumerate(self.disruptions):
            if i not in self._announced and d.announced_s <= self.now_s:
                self._announced.add(i)
                events.add("disruption")
        for i, (d, announced) in enumerate(self.incidents):
            if i not in self._announced_incidents and announced <= self.now_s:
                self._announced_incidents.add(i)
                events.add("disruption")
                self._reopen = True
            if i not in self._revised_incidents and announced <= self.now_s and _revealed(d, self.now_s):
                self._revised_incidents.add(i)
                if d.etr_s is not None:  # what the plans assumed changes: the end is known (issue #156)
                    events.add("disruption")
                    self._reopen = True
        self._events = events
        return events

    def _known_closures(self) -> list[Closure]:
        return [d.closure for d in self.disruptions if d.announced_s <= self.now_s]

    def _reopen_window(self) -> int:
        """An incident became known or its end changed (issue #156): the decisions of the last plan and the
        orders not yet executed were made without it (a train due to leave into a dead OHE zone cannot), so
        they are withdrawn and re-planned; the cycle's options give the new orders. Returns how many orders
        were withdrawn."""
        self.cadence.release(self.now_s)
        out = 0
        for oid, d in list(self._decisions.items()):
            if d.dep_s >= self.now_s and self._in_force(oid):
                self._withdrawn.add(oid)
                out += 1
        if out:
            self._commit()
        return out

    def _known_incidents(self) -> list[Any]:
        """The announced incidents as the controller knows them (issue #156): until it clears or its
        announced ETR passes (the injector then announces the real end), an incident is planned to end at
        its ETR, which stays uncertain (the planner's contingent plans); then at its real end."""
        out = []
        for d, announced in self.incidents:
            if announced > self.now_s:
                continue
            if d.etr_s is not None and not _revealed(d, self.now_s):
                d = d.model_copy(update={"duration_s": max(0.0, d.etr_s - d.start_s)})
            out.append(d)
        return out

    # ------------------------------------------------------------------ one cycle
    def _cycle(self, view: DssView, triggers: list[str]) -> CycleRecord:
        t0 = time.perf_counter()
        now = self.now_s
        closures = self._known_closures()
        targets, blocked = self._releasable(view)
        reopen, self._reopen = self._reopen, False
        withdrawn = self._reopen_window() if reopen else 0
        frozen = self.cadence.freeze([] if reopen else self._last_plan, now)
        holds, lines = self.commitments()
        horizon = now + self.cadence.freeze_s
        ahead = [d for oid, d in self._decisions.items() if d.dep_s >= now and self._in_force(oid)]
        # an order of a contrasting option is imposed whenever it is (the planner does not reproduce it)
        foreign = {(d.run_id, d.station) for oid, d in self._decisions.items() if oid in self._foreign}
        frozen += [d for d in ahead if d.dep_s < horizon or (d.run_id, d.station) in foreign]
        # one decision per train and station: an accepted order overrides the last plan's (issue #152; with
        # a planner that does not reproduce its own earlier decisions the two can differ)
        frozen = list({(d.run_id, d.station): d for d in frozen}.values())
        committed = [d for d in ahead if d.dep_s >= horizon and (d.run_id, d.station) not in foreign]
        past = {(d.run_id, d.station): d for d in self.cadence.history()}
        past |= {
            (d.run_id, d.station): d
            for oid, d in self._decisions.items()
            if d.dep_s < now and self._in_force(oid)
        }
        disruption = "disruption" in triggers or bool(closures)
        limits = self.register.value("opt.time_limit_s")
        ctx = PlanContext(
            now_s=now,
            plan_id=f"plan-{len(self.cycles)}",
            triggers=triggers,
            frozen=frozen,
            committed=committed,
            # the frozen decisions are imposed when the world changed, and when the plan in force is not
            # the planner's own (an accepted contrasting option, issue #196), which it does not reproduce
            impose=bool(closures or self.exit_windows or now < self._foreign_until_s),
            history=list(past.values()),
            closures=closures,
            exit_windows=self.exit_windows,
            commit_holds=holds,
            commit_lines=lines,
            policy_from_s=self.cadence.first_plan_s if self.cadence.first_plan_s is not None else now,
            verify=bool(targets),
            time_limit_s=float(limits["disruption" if disruption else "routine"]),
            previous=list(self._last_plan),
            incidents=self._known_incidents(),
            entry_delays=dict(self.entry_delays),
            commit_terminations=self.terminations(),
            decisions=[(tuple(c.trains), c.resource) for c in targets],
        )
        self._last_ctx = ctx
        planned = self.planner.plan(ctx)
        if withdrawn:
            planned.notes.append(f"incident: commit window re-opened, {withdrawn} orders withdrawn")
        if self.audit is not None:
            self._run_id = self.audit.plan_run(ctx, view, planned.candidates)
        self.cadence.mark(now)
        self._last_plan = planned.decisions
        released = self._release(targets, planned, view, closures) if targets else []
        record = CycleRecord(
            cycle=len(self.cycles),
            now_s=now,
            triggers=triggers,
            plan_id=ctx.plan_id,
            conflicts=len(view.conflicts),
            blocked_by_g0=blocked,
            candidates=[
                CandidateRecord(
                    name=c.name,
                    verified=c.verified,
                    fail=c.fail_rules,
                    warn=c.warn_rules,
                    g3=c.g3.status,
                    kpi_delta=c.delta,
                    solver=c.solver,
                )
                for c in planned.candidates
            ],
            baseline=planned.baseline,
            released=released,
            notes=planned.notes,
            expired=self._cycle_expired,
            wall_s=round(time.perf_counter() - t0, 2),
            planner=planned.planner,
            churn=planned.churn,
            first_plan_s=planned.first_plan_s,
            decisions=self._decision_records,
            contrasts=[
                ContrastRecord(
                    first=c.first,
                    resource=c.resource,
                    verified=c.verified,
                    reversed=c.reversed,
                    cost_min=c.cost_min,
                    fail=c.candidate.fail_rules,
                    answer=c.answer(),
                )
                for c in planned.contrasts
            ],
            contingent=[
                ContingentRecord(
                    name=c.candidate.name,
                    etr_s=c.etr_s,
                    switch_at_s=c.switch_at_s,
                    verified=c.candidate.verified,
                    fail=c.candidate.fail_rules,
                    kpi_delta=c.candidate.delta,
                    rule=c.rule(),
                )
                for c in planned.contingent
            ],
        )
        self._cycle_expired = []
        self.cycles.append(record)
        return record

    def _releasable(self, view: DssView) -> tuple[list[Any], int]:
        """Conflicts to plan for: not already committed, G0 passing before the solve (FR-VER-01)."""
        out, blocked = [], 0
        ranked = sorted(
            (c for c in view.conflicts if c.priority in OPEN_CONFLICT_PRIORITIES),
            key=lambda c: (c.priority, c.time),
        )
        for c in ranked:
            if self._committed(c):
                continue
            basis = self._basis(
                c, f"pre-{len(self.cycles)}", view.version, [d.closure for d in self._announced_now()]
            )
            g0 = gates.g0(basis, self._current(view), self.now_s, stage="pre_solve", register=self.register)
            if not g0.passed:
                blocked += 1
                continue
            out.append(c)
        return out[: self.max_conflicts], blocked

    def _current(self, view: DssView) -> gates.StateSnapshot:
        """``S_t`` for G0: the state version names the snapshot; closures announced are active."""
        return view.snapshot.model_copy(
            update={
                "snapshot_hash": f"v{view.version}",
                "active_disruptions": [self._closure_name(d.closure) for d in self._announced_now()],
            }
        )

    def _announced_now(self) -> list[Disruption]:
        return [d for d in self.disruptions if d.announced_s <= self.now_s]

    def _basis(self, c: Any, plan_id: str, version: int, closures: list[Closure]) -> gates.PlanBasis:
        known = self._view.known_trains if self._view is not None else frozenset(c.trains)
        return gates.PlanBasis(
            plan_id=plan_id,
            network_version=self._network_version(),
            snapshot_hash=f"v{version}",
            trains=[t for t in c.trains if t in known],
            assumed_disruptions=[self._closure_name(x) for x in closures],
        )

    def _network_version(self) -> str:
        return self._view.snapshot.network_version if self._view is not None else ""

    @staticmethod
    def _closure_name(c: Closure) -> str:
        return f"{c.segment}/{c.line_id or '*'}@{c.start_s:g}-{c.end_s:g}"

    def _committed(self, conflict: Any) -> bool:
        prefix = self._context(conflict, "")
        return any(str(r.context).startswith(prefix) and r.status in COMMITTED for r in self.lc.recs.values())

    @staticmethod
    def _context(conflict: Any, suffix: str) -> str:
        return f"type{conflict.type}|{'/'.join(conflict.trains)}|{conflict.resource}|{suffix}"

    # ------------------------------------------------------------------ release
    def _release(
        self, targets: list[Any], planned: PlanSet, view: DssView, closures: list[Closure]
    ) -> list[OptionRecord]:
        ranked = sorted(
            (c for c in planned.candidates if c.verified), key=lambda c: (c.delta.get(KPI_RANK, 0.0), c.name)
        )
        names = [self._closure_name(c) for c in closures]
        holds, lines = self.commitments()
        out: list[OptionRecord] = []
        self._decision_records = []
        for conflict in targets:
            options: list[tuple[Candidate, list[PlanAction]]] = []
            seen: set[str] = set()
            addressing: list[Candidate] = []  # verified plans with orders for it, released or in force
            for cand in ranked:
                every = cand.actions_for(conflict.trains, conflict.resource)
                if every:
                    addressing.append(cand)
                actions = [x for x in every if not self._in_orders(x, holds, lines)]
                sig = "|".join(sorted(a.model_dump_json() for a in actions))
                if actions and sig not in seen:
                    seen.add(sig)
                    options.append((cand, actions))
            options = options[:MAX_OPTIONS]
            # the precedences on offer: the released options and the orders already in force (issue #196)
            firsts = {order_of(c.decisions, conflict.trains, conflict.resource)[0] for c in addressing}
            if options:  # a decision released (one whose orders are all in force has no options)
                self._decision_records.append(
                    DecisionRecord(
                        trains=list(conflict.trains),
                        resource=conflict.resource,
                        options=len(options),
                        precedences=len(firsts),
                    )
                )
            self.max_options = max(self.max_options, len(options))
            built: list[tuple[int, Candidate, Option]] = []
            for rank, (cand, actions) in enumerate(options, start=1):
                option = Option(
                    option_id=f"{cand.plan_id}:{cand.name}:{conflict.trains[0]}",
                    actions=actions,
                    authored_by=_author(cand),
                )
                self._entries[option.option_id] = _Entry(
                    cand, tuple(conflict.trains), view.version, names, self._network_version()
                )
                built.append((rank, cand, option))
            ids: dict[int, str] = {}
            for rank, cand, option in built:
                self._ids += 1
                rec_id = f"REC-{self._ids:04d}"
                ids[rank] = rec_id
                self._alternatives[rec_id] = [o for r, _, o in built if r != rank]
                context: Hashable = self._context(conflict, f"opt{rank}")
                verdict = self.executor.present(rec_id, context, option)
                rec = self.lc.recs[rec_id]
                out.append(
                    OptionRecord(
                        rec_id=rec_id,
                        context=str(context),
                        rank=rank,
                        candidate=cand.name,
                        actions=[a.phrase() for a in option.actions],
                        kpi_delta=cand.delta,
                        status=str(rec.status),
                        warn=verdict.warn,
                    )
                )
            self._audit_recommendations(conflict, built, ids, out, view)
            if self.auto_accept and 1 in ids and self.lc.recs[ids[1]].status is Status.PRESENTED:
                self._accept(ids[1], out)
        return out

    @staticmethod
    def _in_orders(
        a: PlanAction, holds: Mapping[tuple[str, str], float], lines: Mapping[tuple[str, str], int]
    ) -> bool:
        """An order already in force is not recommended again."""
        key = (a.run_id, a.station)
        if a.kind in END_KINDS:
            return False  # a train is short-terminated by one order only: its option is not rebuilt
        return holds.get(key) == a.until_s if a.kind == "hold_until" else lines.get(key) == a.line_no

    def accept(self, rec_id: str, controller: str, warn_reason: str | None = None) -> Status:
        """A controller accepts (FR-HITL-03); the audit trail names them only by pseudonym."""
        return self._accept(rec_id, [], actor=self._who(controller), warn_reason=warn_reason)

    def reject(self, rec_id: str, reason_code: str, controller: str, note: str = "") -> None:
        """A controller rejects with a reason code (FR-HITL-05)."""
        self.lc.advance(self.now_s)
        self.executor.reject(rec_id, reason_code, note)
        self._events.add("controller_action")
        self._audit_decision(
            rec_id, "reject", self._who(controller), f"{reason_code} {note}".strip(), "REJECTED"
        )

    def _who(self, controller: str) -> str:
        return pseudonym(controller, self.audit.log.salt if self.audit is not None else "raildss")

    def _accept(
        self,
        rec_id: str,
        out: list[OptionRecord],
        *,
        actor: str = EVALUATION_POLICY,
        warn_reason: str | None = None,
    ) -> Status:
        verdict = self.executor.verdicts[rec_id]
        reason = warn_reason or (
            "auto-accept (evaluation)" if verdict.warn and actor == EVALUATION_POLICY else None
        )
        status = self.executor.accept(rec_id, reason)
        self._audit_decision(
            rec_id,
            "accept",
            actor,
            reason or ("evaluation policy: best option" if actor == EVALUATION_POLICY else "accepted"),
            str(status),
        )
        for o in out:
            if o.rec_id == rec_id:
                o.status = str(status)
        self._events.add("controller_action")
        if status is Status.ORDER_ISSUED:
            entry = self._entries[self.executor.options[rec_id].option_id]
            cand = entry.candidate
            # a greedy plan is exact in its own world; a solver plan is rounded by the IR, so freeze what
            # executing it makes happen (the sandbox replay) rather than the rounded times (VER-FRZ-01)
            made = cand.decisions if cand.g1 is None else cand.g3.departures
            realised = {(d.run_id, d.station): d for d in made}
            # the world now follows the accepted option: it is the plan the next cycle freezes (issue #152;
            # the planner's own plan may be another option)
            self._last_plan = [Decision(d.run_id, d.station, d.line_no, d.dep_s) for d in made]
            for oid, action in zip(
                self.executor.current[rec_id], self.executor.options[rec_id].actions, strict=True
            ):
                if action.kind in END_KINDS:  # short-terminate or cancel (issue #156)
                    self._ends[oid] = (action.run_id, action.station)
                    continue
                d = realised.get((action.run_id, action.station))
                if d is not None:
                    self._decisions[oid] = Decision(d.run_id, d.station, d.line_no, d.dep_s)
                    if cand.name not in ("greedy", "cpsat"):  # an order the planner does not reproduce
                        self._foreign_until_s = max(self._foreign_until_s, d.dep_s)
                        self._foreign.add(oid)
            self._commit()
        return status

    def _commit(self) -> None:
        """The orders in force change the world the feed observes from now on."""
        holds, lines = self.commitments()
        ends = self.terminations()
        if ends:
            self.dss.commit(holds, lines, ends)
        else:
            self.dss.commit(holds, lines)

    # ------------------------------------------------------------------ the audit trail
    def _audit_view(self, view: DssView) -> None:
        if self.audit is None:
            return
        for c in view.conflicts:
            if c.priority in OPEN_CONFLICT_PRIORITIES and c.key not in self._audited:
                self._audited.add(c.key)
                self.audit.conflict(self.now_s, c)
        for a in view.alerts:
            self.audit.alert(self.now_s, str(a["kind"]), str(a["detail"]), str(a["priority"]))

    def _audit_sweep(self) -> None:
        if self.audit is None:
            return
        for a in self.executor.alerts[self._executor_alerts :]:
            self.audit.alert(self.now_s, "executor", a.text, a.priority)
        self._executor_alerts = len(self.executor.alerts)
        self.audit.outcomes(self.now_s, self.lc, self._rec_trains)

    def _audit_recommendations(
        self,
        conflict: Any,
        built: list[tuple[int, Candidate, Option]],
        ids: dict[int, str],
        out: list[OptionRecord],
        view: DssView,
    ) -> None:
        if self.audit is None:
            return
        shown = [
            {
                "rec_id": ids[rank],
                "rank": rank,
                "candidate": cand.name,
                "explanation": [a.phrase() for a in option.actions],
                "kpi_delta": cand.delta,
            }
            for rank, cand, option in built
        ]
        for rank, cand, option in built:
            rec_id = ids[rank]
            self._presented_s[rec_id] = self.now_s
            self._rec_trains[rec_id] = tuple(conflict.trains)
            self.audit.recommendation(
                self.now_s,
                rec_id=rec_id,
                run_id=self._run_id,
                conflict=conflict,
                rank=rank,
                cand=cand,
                actions=option.actions,
                shown=shown,
                view=view,
                verdict_warn=self.executor.verdicts[rec_id].warn,
                valid_until_s=self.lc.recs[rec_id].valid_until,
            )

    def _audit_decision(self, rec_id: str, action: str, actor: str, reason: str, status: str) -> None:
        if self.audit is None:
            return
        option = self.executor.options[rec_id]
        entry = self._entries.get(option.option_id)
        diff: dict[str, Any] = {"actions": [a.phrase() for a in option.actions]}
        if entry is not None:
            diff["kpi_delta"] = entry.candidate.delta
        self.audit.decision(
            self.now_s,
            rec_id=rec_id,
            action=action,
            actor=actor,
            trains=self._rec_trains.get(rec_id, ()),
            reason=reason,
            presented_s=self._presented_s.get(rec_id),
            diff=diff,
            status=status,
        )

    # ------------------------------------------------------------------ the executor's callbacks
    def _verify(self, option: Option) -> Verdict:
        """The release policy and the pre-execution check: G0 against the latest state, then the
        cached G1-G3 verdict of the plan the option came from."""
        entry = self._entries.get(option.option_id)
        if entry is None or self._view is None:
            return Verdict(fail=["UNVERIFIED"])
        cand, view = entry.candidate, self._view
        known = view.known_trains
        basis = gates.PlanBasis(
            plan_id=cand.plan_id,
            network_version=entry.network_version,
            snapshot_hash=f"v{entry.version}",
            trains=[t for t in entry.trains if t in known],
            assumed_disruptions=entry.closures,
        )
        g0 = gates.g0(basis, self._current(view), self.now_s, stage="pre_execution", register=self.register)
        fail = [f"G0:{f.check}" for f in g0.findings] + cand.fail_rules
        return Verdict(fail=sorted(set(fail)), warn=cand.warn_rules)

    def _replan(self, option: Option, declined: list[PlanAction]) -> Option | None:
        """FR-HITL-09: the next verified option of the decision that does not repeat a declined action."""
        banned = {(a.kind, a.run_id, a.station, a.line_no, a.until_s) for a in declined}
        rec_id = next((r for r, o in self.executor.options.items() if o is option), None)
        for alt in self._alternatives.get(rec_id or "", []):
            if all((a.kind, a.run_id, a.station, a.line_no, a.until_s) not in banned for a in alt.actions):
                return alt
        return None

    def _publish(self, topic: str, payload: dict[str, Any]) -> None:
        self.published.append((topic, payload))
        if self.audit is not None and topic == "ctrl.orders":
            self.audit.order(self.now_s, payload)
        if topic == "ctrl.orders":
            self._acks.append((self.now_s + self.ack_latency_s, str(payload["order_id"])))

    def _answer_acks(self) -> None:
        due = [a for a in self._acks if a[0] <= self.now_s]
        self._acks = [a for a in self._acks if a[0] > self.now_s]
        for _, order_id in due:
            order = self.executor.orders.get(order_id)
            if order is None or order.status != "ISSUED":
                continue
            payload = self.published_order(order_id)
            declined = self.decline is not None and self.decline(payload)
            if self.audit is not None:
                self.audit.ack(
                    self.now_s,
                    order_id,
                    "DECLINED" if declined else "ACKNOWLEDGED",
                    "SM_DECLINE" if declined else None,
                    payload,
                )
            self.executor.on_ack(
                {
                    "order_id": order_id,
                    "status": "DECLINED" if declined else "ACKNOWLEDGED",
                    "reason_code": "SM_DECLINE" if declined else None,
                }
            )
            if declined:
                self._events.add("order_decline")
                self._commit()

    def published_order(self, order_id: str) -> dict[str, Any]:
        return next(p for t, p in self.published if t == "ctrl.orders" and p["order_id"] == order_id)

    def why_not(self, trains: Sequence[str], first: str, resource: str) -> Contrast | None:
        """FR-OPT-06 (issue #154): 'why not ``first`` first at ``resource``?' in the last plan's context: the
        re-solve with that precedence imposed, verified, and its cost difference, or the rules that forbid
        it. None when the planner cannot re-solve."""
        solve = getattr(self.planner, "why_not", None)
        if solve is None or self._last_ctx is None:
            return None
        result: Contrast = solve(self._last_ctx, trains, first, resource)
        return result

    # ------------------------------------------------------------------ orders in force
    def _in_force(self, order_id: str) -> bool:
        order = self.executor.orders.get(order_id)
        return order is not None and order.status != "DECLINED" and order_id not in self._withdrawn

    def commitments(self) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], int]]:
        """Holds and lines of the orders in force (issued or acknowledged, not declined)."""
        holds: dict[tuple[str, str], float] = {}
        lines: dict[tuple[str, str], int] = {}
        for oid, d in self._decisions.items():
            if not self._in_force(oid):
                continue
            order = self.executor.orders[oid]
            if order.action.kind == "hold_until" and order.action.until_s is not None:
                holds[(d.run_id, d.station)] = order.action.until_s
            elif order.action.line_no is not None:
                lines[(d.run_id, d.station)] = order.action.line_no
        return holds, lines

    def terminations(self) -> dict[str, str]:
        """Short-terminations and cancellations in force: run id -> station (issue #156)."""
        return {rid: station for oid, (rid, station) in self._ends.items() if self._in_force(oid)}

    # ------------------------------------------------------------------ result
    def summary(self, from_s: float, to_s: float) -> RunSummary:
        statuses: dict[str, int] = {}
        for r in self.lc.recs.values():
            statuses[str(r.status)] = statuses.get(str(r.status), 0) + 1
        replans: list[ReplanRecord] = self.executor.replans
        return RunSummary(
            from_s=from_s,
            to_s=to_s,
            ticks=self.ticks,
            cycles=self.cycles,
            replan_starts_s=list(self.cadence.starts),
            max_replan_gap_s=self.cadence.max_gap_s,
            released=len(self.lc.recs),
            accepted=sum(1 for r in self.lc.recs.values() if r.decided),
            expired=statuses.get("EXPIRED", 0),
            superseded=statuses.get("SUPERSEDED", 0),
            orders=len(self.executor.orders),
            declined=sum(1 for o in self.executor.orders.values() if o.status == "DECLINED"),
            alerts=[f"{a.priority} {a.text}" for a in self.executor.alerts],
            replans=[r.model_dump() for r in replans],
            statuses=statuses,
            max_options_per_decision=self.max_options,
        )


def _revealed(d: Any, now_s: float) -> bool:
    """The incident's real end is known: it cleared, or its announced ETR passed (the revision)."""
    return now_s >= d.end_s or (d.etr_s is not None and now_s >= d.etr_s)


def _author(cand: Candidate) -> Literal["optimiser", "greedy"]:
    return "greedy" if cand.name == "greedy" else "optimiser"
