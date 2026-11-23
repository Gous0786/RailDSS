"""Disruption options in the loop (issue #156, FR-OPT-11): short-terminate / cancel, contingent ETR plans,
the announced ETR the plans assume, with a scripted DSS and planner."""

from __future__ import annotations

from typing import Any

from raildss_hitl.executor import PlanAction
from raildss_hitl.lifecycle import Status
from raildss_optimiser.greedy import Decision, Hold
from raildss_orchestrator.model import KPI_RANK, Candidate, Contingent, PlanContext, PlanSet
from raildss_twin.injector import Disruption

from .test_loop import MIN, NOW0, FakeConflict, FakeDss, candidate, g3, make


def short_term(delta: float, penalty: float, *, cancel: bool = False) -> Candidate:
    return Candidate(
        name="cancel" if cancel else "short-term",
        plan_id="plan",
        decisions=[Decision("B", "S2", 1, NOW0 + 300)],
        holds=[Hold("B", "S2", NOW0 + 300, 60.0)],
        g1=None,
        g2=None,
        g3=g3(delta),
        terminations={"A": "S1"},
        cancelled=frozenset({"A"}) if cancel else frozenset(),
        penalty_min=penalty,
    )


def test_a_short_termination_is_ranked_with_its_penalty_and_ordered_first() -> None:
    cand = short_term(-50.0, 120.0)
    assert cand.delta[KPI_RANK] == 70.0  # -50 delay-min of the passengers + 120 for the terminated train
    actions = cand.actions_for(("A", "B"), "S1~S2")
    assert actions[0] == PlanAction(kind="short_terminate", run_id="A", station="S1")
    assert actions[0].phrase() == "SM/S1: short-terminate A"
    assert [a.run_id for a in actions[1:]] == ["B"]  # the other train's hold
    cancel = short_term(-50.0, 120.0, cancel=True).actions_for(("A",), "S1~S2")
    assert cancel[0].kind == "cancel" and cancel[0].phrase().startswith("Control: cancel A")


def test_the_contingent_switch_rule_names_the_decision_time() -> None:
    c = Contingent(etr_s=11 * 3600.0, switch_at_s=10.5 * 3600, candidate=candidate("x", 0.0, hold_until=NOW0))
    assert c.rule().startswith("if not restored by 10:30, switch to the plan for ETR 11:00")


def test_an_accepted_short_termination_is_an_order_that_changes_the_world() -> None:
    class Dss(FakeDss):
        def __init__(self) -> None:
            super().__init__()
            self.ends: list[Any] = []

        def commit(self, holds: Any, lines: Any, terminations: Any = None) -> None:
            super().commit(holds, lines)
            self.ends.append(terminations)

    orch, _, planner = make([short_term(-200.0, 120.0)])
    dss = Dss()
    orch.dss = dss
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None and cycle.released[0].status == Status.ORDER_ISSUED
    assert cycle.released[0].actions[0] == "SM/S1: short-terminate A"
    assert orch.terminations() == {"A": "S1"} and dss.ends[-1] == {"A": "S1"}
    orch.tick(NOW0 + 2 * MIN)  # the next plan keeps it
    assert planner.contexts[-1].commit_terminations == {"A": "S1"}


def test_plans_assume_the_announced_etr_until_it_passes() -> None:
    etr = NOW0 + 30 * MIN
    d = Disruption(
        incident_id="OHE",
        kind="ohe_block",
        start_s=NOW0,
        duration_s=90 * MIN,  # it overruns the announced ETR by an hour
        etr_s=etr,
        block_id="GVI-CPP",
    )
    orch, _, planner = make([], incidents=((d, NOW0),))
    orch.run(NOW0, NOW0 + 40 * MIN, 60.0)
    first, *_ = planner.contexts
    assert first.incidents[0].end_s == etr and "disruption" in first.triggers
    late = [c for c in planner.contexts if c.now_s >= etr]
    assert late[0].now_s == etr and "disruption" in late[0].triggers  # the revision is a trigger
    assert late[0].incidents[0].end_s == d.end_s  # and plans now know the real end


def test_an_incident_reopens_the_commit_window() -> None:
    d = Disruption(
        incident_id="TRIP", kind="ohe_trip", start_s=NOW0 + 4 * MIN, duration_s=30 * MIN, block_id="GVI-CPP"
    )
    orch, dss, planner = make(
        [candidate("greedy", -5.0, hold_until=NOW0 + 20 * MIN)], incidents=((d, d.start_s),)
    )
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    orch.tick(NOW0)
    assert orch.commitments()[0] == {("A", "S1"): NOW0 + 20 * MIN}  # an order made before the incident
    orch.tick(NOW0 + 2 * MIN)
    assert planner.contexts[-1].committed  # the order is kept while nothing changes
    cycle = orch.tick(NOW0 + 4 * MIN)  # the incident is announced
    assert cycle is not None and "disruption" in cycle.triggers
    ctx = planner.contexts[-1]
    assert ctx.frozen == [] and ctx.committed == []  # made without the incident: re-planned
    assert any("1 orders withdrawn" in n for n in cycle.notes)


class _Contingent:
    """A planner that returns one contingent plan (to see it in the cycle record)."""

    def __init__(self) -> None:
        self.contexts: list[PlanContext] = []

    def plan(self, ctx: PlanContext) -> PlanSet:
        self.contexts.append(ctx)
        cand = candidate("contingent-etr+30", -1.0, hold_until=ctx.now_s + 600)
        return PlanSet(
            decisions=[],
            candidates=[candidate("greedy", -1.0, hold_until=ctx.now_s + 600)] if ctx.verify else [],
            contingent=[Contingent(etr_s=ctx.now_s + 3600, switch_at_s=ctx.now_s + 1800, candidate=cand)],
            first_plan_s=1.5,
        )


def test_the_cycle_records_the_contingent_plans_and_the_first_plan_latency() -> None:
    orch, dss, _ = make([])
    orch.planner = _Contingent()
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None and cycle.first_plan_s == 1.5
    (rec,) = cycle.contingent
    assert (
        rec.verified and rec.name == "contingent-etr+30" and rec.rule.startswith("if not restored by 08:30")
    )


def test_the_four_disruption_scenarios_run_the_loop() -> None:
    from raildss_orchestrator.scenarios import loopable
    from raildss_twin import scenario

    library = {s.id: s for s in scenario.load_library_v1()}
    assert all(loopable(library[i]) for i in ("SCN-04", "SCN-08", "SCN-09", "SCN-10"))
