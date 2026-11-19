"""The loop with a scripted DSS and planner: release policy, supersession, expiry, declines (issue #84)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import pytest

from raildss_audit.chain import AuditLog, pseudonym
from raildss_hitl.lifecycle import Status
from raildss_optimiser.greedy import Decision, Hold
from raildss_orchestrator.auditing import AuditRecorder
from raildss_orchestrator.loop import Disruption, Orchestrator
from raildss_orchestrator.model import (
    KPI_RANK,
    MAX_OPTIONS,
    Candidate,
    DssView,
    PlanContext,
    PlanSet,
)
from raildss_params import load
from raildss_twin.sandbox import Departure, G3Result
from raildss_twin.sim import Closure
from raildss_verifier import gates

NOW0 = 8 * 3600.0
MIN = 60.0
T0 = dt.datetime(2025, 8, 20, 2, 30, tzinfo=dt.UTC)


@dataclass
class FakeConflict:
    type: int
    trains: tuple[str, ...]
    resource: str
    time: dt.datetime = T0
    priority: str = "P2"
    near: bool = False

    @property
    def key(self) -> tuple[Any, ...]:
        return self.type, self.trains, self.resource


def g3(delta: float, *, fail: bool = False, departures: list[Departure] | None = None) -> G3Result:
    return G3Result(
        plan_id="p",
        day=dt.date(2025, 8, 20),
        start_s=NOW0,
        end_s=NOW0 + 7200,
        status="FAIL" if fail else "PASS",
        violations=[]
        if not fail
        else [
            {"rule_id": "VER-CON-01", "trains": ["A"], "time_s": NOW0, "detail": "x", "source": "replay"}  # type: ignore[list-item]
        ],
        warnings=0,
        kpis={"baseline": {}, "plan": {}, "delta": {KPI_RANK: delta}},
        trace_hash="t",
        baseline_trace_hash="b",
        departures=departures or [],
    )


def candidate(name: str, delta: float, *, hold_until: float, fail: bool = False) -> Candidate:
    dec = Decision("A", "S1", 2, hold_until)
    return Candidate(
        name=name,
        plan_id="plan",
        decisions=[dec],
        holds=[Hold("A", "S1", hold_until, 120.0)],
        g1=None,
        g2=None,
        g3=g3(
            delta, fail=fail, departures=[Departure(run_id="A", station="S1", line_no=2, dep_s=hold_until)]
        ),
    )


class FakeDss:
    def __init__(self) -> None:
        self.script: dict[float, list[FakeConflict]] = {}
        self.fix_age = 10.0
        self.version = 1
        self.commits = 0

    def advance(self, now_s: float) -> DssView:
        conflicts = [c for t, cs in sorted(self.script.items()) if t <= now_s for c in cs]
        fixes = {t: now_s - self.fix_age for t in ("A", "B")}
        snap = gates.StateSnapshot(
            snapshot_hash="x", network_version="N", taken_at_s=now_s, train_last_fix_s=fixes
        )
        return DssView(
            now_s, self.version, snap, frozenset(fixes), conflicts, state_hash=f"hash-v{self.version}"
        )  # type: ignore[arg-type]

    def commit(self, holds: Any, lines: Any) -> None:
        self.commits += 1


class FakePlanner:
    def __init__(self, cands: list[Candidate]) -> None:
        self.cands = cands
        self.contexts: list[PlanContext] = []

    def plan(self, ctx: PlanContext) -> PlanSet:
        self.contexts.append(ctx)
        return PlanSet(
            decisions=[Decision("Z", "S9", 1, ctx.now_s + 5 * MIN)],
            candidates=self.cands if ctx.verify else [],
            baseline={"passenger_arrival_delay_min": 50.0},
        )


def make(cands: list[Candidate], **kw: Any) -> tuple[Orchestrator, FakeDss, FakePlanner]:
    dss, planner = FakeDss(), FakePlanner(cands)
    return Orchestrator(load(), dss, planner, **kw), dss, planner


def test_replans_follow_od_25_on_a_simulated_clock() -> None:
    orch, dss, _ = make([])
    dss.script = {NOW0 + 7 * MIN: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    summary = orch.run(NOW0, NOW0 + 30 * MIN, 60.0)
    starts = [(s - NOW0) / MIN for s in summary.replan_starts_s]
    assert starts == [0, 5, 7, 12, 17, 22, 27]  # every 5 min and at once for the new conflict
    assert summary.max_replan_gap_s <= 5 * MIN
    assert summary.cycles[2].triggers == ["new_conflict"]


def test_a_closure_is_a_disruption_trigger_when_it_is_announced() -> None:
    closure = Closure("S1~S2", "UP", NOW0 + 40 * MIN, NOW0 + 60 * MIN)
    orch, _, planner = make([], disruptions=(Disruption(closure, NOW0 + 3 * MIN),))
    summary = orch.run(NOW0, NOW0 + 9 * MIN, 60.0)
    assert [(s - NOW0) / MIN for s in summary.replan_starts_s] == [0, 3, 8]
    assert planner.contexts[0].closures == []
    assert planner.contexts[1].closures == [closure]
    assert planner.contexts[1].time_limit_s == 60.0  # the disruption budget (opt.time_limit_s)
    assert planner.contexts[0].time_limit_s == 15.0


def test_at_most_three_verified_options_are_released_and_the_failing_one_never() -> None:
    cands = [
        candidate("greedy", -5.0, hold_until=NOW0 + 600),
        candidate("cpsat", -9.0, hold_until=NOW0 + 700),
        candidate("cpsat-alt1", -7.0, hold_until=NOW0 + 800),
        candidate("cpsat-alt2", -8.0, hold_until=NOW0 + 900),
        candidate("bad", -99.0, hold_until=NOW0 + 1000, fail=True),
    ]
    orch, dss, _ = make(cands, auto_accept=False)
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None
    released = cycle.released
    assert len(released) == MAX_OPTIONS
    assert [r.candidate for r in released] == ["cpsat", "cpsat-alt2", "cpsat-alt1"]  # best KPI delta first
    assert all(r.status == Status.PRESENTED for r in released)
    assert {c.name: c.verified for c in cycle.candidates}["bad"] is False
    assert cycle.baseline == {"passenger_arrival_delay_min": 50.0}  # the baseline travels with the options
    assert orch.max_options == MAX_OPTIONS


def test_a_newer_option_supersedes_the_open_one_and_an_unanswered_one_expires() -> None:
    orch, dss, _ = make([candidate("greedy", -5.0, hold_until=NOW0 + 600)], auto_accept=False)
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    orch.tick(NOW0)
    first = next(iter(orch.lc.recs))
    dss.script[NOW0 + 2 * MIN] = [FakeConflict(1, ("B", "A"), "S2~S3")]
    orch.tick(NOW0 + 2 * MIN)  # a new conflict re-plans at once and presents the option again
    assert orch.lc.recs[first].status is Status.SUPERSEDED
    open_now = [r.rec_id for r in orch.lc.recs.values() if r.status is Status.PRESENTED]
    assert open_now
    # nobody acts: valid_until is one re-plan interval after the presentation, then each one expires
    last = max(orch.lc.recs[r].valid_until for r in open_now)
    orch.tick(last + 60.0)
    assert {orch.lc.recs[r].status for r in open_now} == {Status.EXPIRED}


def test_a_stale_train_blocks_the_release_before_the_solve() -> None:
    orch, dss, planner = make([candidate("greedy", -5.0, hold_until=NOW0 + 600)])
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    dss.fix_age = 300.0  # no position fix for 5 min: stale (state.stale_threshold_min)
    cycle = orch.tick(NOW0)
    assert cycle is not None and cycle.blocked_by_g0 == 1 and cycle.released == []
    assert planner.contexts[0].verify is False  # nothing to release: only the rolling greedy plan runs


def test_headless_accept_issues_orders_and_freezes_the_replayed_departure() -> None:
    orch, dss, planner = make([candidate("greedy", -5.0, hold_until=NOW0 + 600)])
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None
    assert cycle.released[0].status == Status.ORDER_ISSUED
    assert [t for t, _ in orch.published] == ["ctrl.orders"]
    assert dss.commits == 1
    holds, _ = orch.commitments()
    assert holds == {("A", "S1"): NOW0 + 600}
    orch.tick(NOW0 + 2 * MIN)  # the station master acknowledges after hitl.sm_ack_latency_s
    assert next(iter(orch.lc.recs.values())).status in (Status.EXECUTING, Status.ACKNOWLEDGED)
    # the accepted order was a trigger (controller_action): the tick re-planned and froze its departure
    assert orch.cadence.starts == [NOW0, NOW0 + 2 * MIN]
    assert any(d.run_id == "A" and d.dep_s == NOW0 + 600 for d in planner.contexts[-1].frozen)


def test_a_declined_order_raises_a_trigger_and_is_replanned_with_the_next_option() -> None:
    cands = [
        candidate("greedy", -9.0, hold_until=NOW0 + 600),
        candidate("cpsat", -5.0, hold_until=NOW0 + 700),
    ]
    orch, dss, _ = make(cands, decline=lambda order: order["until_s"] == NOW0 + 600)
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    orch.tick(NOW0)
    cycle = orch.tick(NOW0 + 2 * MIN)  # the acknowledgement time: the first order is declined
    summary = orch.summary(NOW0, NOW0 + 2 * MIN)
    assert summary.declined >= 1
    assert summary.replans and summary.replans[0]["option_id"] is not None  # FR-HITL-09
    assert cycle is not None and "order_decline" in cycle.triggers
    holds, _ = orch.commitments()
    assert ("A", "S1") not in holds or holds[("A", "S1")] != NOW0 + 600  # the declined hold is withdrawn


@pytest.mark.parametrize("auto", [True, False])
def test_released_options_always_carry_a_verdict_without_fail(auto: bool) -> None:
    orch, dss, _ = make([candidate("greedy", -5.0, hold_until=NOW0 + 600, fail=True)], auto_accept=auto)
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None and cycle.released == [] and orch.lc.recs == {}


def audited(cands: list[Candidate], **kw: Any) -> tuple[Orchestrator, FakeDss, AuditLog]:
    log = AuditLog(None, "KTV-PSA")
    rec = AuditRecorder(log, load(), T0, day=dt.date(2025, 8, 20))
    orch, dss, _ = make(cands, audit=rec, **kw)
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    return orch, dss, log


def test_the_loop_audits_alerts_recommendations_decisions_orders_acks_and_outcomes() -> None:
    orch, _, log = audited([candidate("greedy", -5.0, hold_until=NOW0 + 600)])
    orch.tick(NOW0)
    orch.tick(NOW0 + 2 * MIN)  # the acknowledgement
    kinds = {r.kind for r in log.records()}
    assert {"alert", "plan_run", "recommendation", "decision", "order", "ack"} <= kinds
    assert log.verify().ok
    decision = log.query(kind="decision")[0]
    assert decision.actor == "evaluation-policy" and decision.payload["action"] == "accept"  # NFR-AUD-02
    assert decision.payload["time_to_decision_s"] == 0.0 and decision.payload["diff"]["actions"]
    rec = log.query(kind="recommendation")[0]
    for key in ("options", "state", "versions", "verification", "explanation", "plan_run"):
        assert rec.payload[key], key
    assert rec.payload["seed"] == 0  # the twin seed (twin.sim.default_seed)
    assert rec.payload["state"]["hash"] == "hash-v1"
    assert log.query(train="A")  # by train
    assert log.query(incident=rec.refs["incident"])  # by incident
    assert len(log.query(rec_id=rec.refs["rec_id"])) >= 3  # recommendation, decision, order, ack, outcome


def test_every_presented_recommendation_has_a_full_record() -> None:
    cands = [
        candidate("greedy", -5.0, hold_until=NOW0 + 600),
        candidate("cpsat", -9.0, hold_until=NOW0 + 700),
    ]
    orch, _, _ = audited(cands, auto_accept=False)
    orch.tick(NOW0)
    assert len(orch.lc.recs) == 2
    assert orch.audit is not None and orch.audit.completeness(orch.lc) == []
    plan_run = orch.audit.log.query(kind="plan_run")[0]
    assert plan_run.payload["digest"] and plan_run.payload["context"]["now_s"] == NOW0


def test_a_controller_reject_and_accept_are_audited_under_a_pseudonym() -> None:
    cands = [
        candidate("greedy", -5.0, hold_until=NOW0 + 600),
        candidate("cpsat", -9.0, hold_until=NOW0 + 700),
    ]
    orch, _, log = audited(cands, auto_accept=False)
    orch.tick(NOW0)
    first, second = list(orch.lc.recs)
    orch.now_s = NOW0 + 30
    orch.reject(first, "UNSAFE_LOCAL", "ravi", "single line occupied")
    orch.accept(second, "ravi")
    decisions = log.query(kind="decision")
    assert [d.payload["action"] for d in decisions] == ["reject", "accept"]
    assert {d.actor for d in decisions} == {pseudonym("ravi", log.salt)}
    assert decisions[0].payload["time_to_decision_s"] == 30.0
    assert "ravi" not in "".join(r.model_dump_json() for r in log.records())
    assert log.query(controller="ravi") == decisions
