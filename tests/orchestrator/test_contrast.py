"""Contrasting options and 'why not X' (issue #154, FR-OPT-05/06)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_optimiser.greedy import Decision, Hold
from raildss_orchestrator.loop import Orchestrator
from raildss_orchestrator.model import Candidate, Contrast, PlanContext, PlanSet, order_of
from raildss_orchestrator.twin import TwinDss, TwinPlanner
from raildss_twin import run

from .test_loop import NOW0, FakeConflict, g3, make


def _cand(name: str, first: str, second: str, delta: float) -> Candidate:
    """A plan that sends ``first`` into S1~S2 at once and holds ``second`` 10 min."""
    return Candidate(
        name=name,
        plan_id="plan",
        decisions=[Decision(first, "S1", 1, NOW0 + 60), Decision(second, "S2", 2, NOW0 + 660)],
        holds=[Hold(second, "S2", NOW0 + 660, 600.0)],
        g1=None,
        g2=None,
        g3=g3(delta),
    )


def test_the_order_a_plan_serves_a_decision_in() -> None:
    plan = _cand("greedy", "A", "B", 0.0)
    assert order_of(plan.decisions, ("A", "B"), "S1~S2") == ["A", "B"]
    assert order_of(plan.decisions, ("B", "A"), "S1~S2") == ["A", "B"]
    assert order_of(plan.decisions, ("A", "C"), "S1~S2") == ["A", "C"]  # C has no departure there: last


def test_a_decision_gets_options_with_either_precedence() -> None:
    orch, dss, _ = make([_cand("greedy", "A", "B", -5.0), _cand("contrast:B", "B", "A", -3.0)])
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    cycle = orch.tick(NOW0)
    assert cycle is not None
    (dec,) = cycle.decisions
    assert dec.options == 2 and dec.precedences == 2  # the options differ in who goes first
    assert [r.candidate for r in cycle.released] == ["greedy", "contrast:B"]  # ranked by the KPI delta


class _WhyNot:
    def __init__(self) -> None:
        self.asked: list[tuple[Any, ...]] = []

    def plan(self, ctx: PlanContext) -> PlanSet:
        return PlanSet(decisions=[], candidates=[_cand("greedy", "A", "B", -5.0)] if ctx.verify else [])

    def why_not(self, ctx: PlanContext, trains: Any, first: str, resource: str) -> Contrast:
        self.asked.append((ctx.plan_id, tuple(trains), first, resource))
        cand = _cand(f"contrast:{first}", first, "A", 4.0)
        return Contrast(first=first, resource=resource, candidate=cand, reversed=True, cost_min=9.0)


def test_why_not_re_solves_in_the_last_plan_context() -> None:
    orch, dss, _ = make([])
    assert orch.why_not(("A", "B"), "B", "S1~S2") is None  # no plan yet
    orch.planner = _WhyNot()
    dss.script = {NOW0: [FakeConflict(2, ("A", "B"), "S1~S2")]}
    orch.tick(NOW0)
    ans = orch.why_not(("A", "B"), "B", "S1~S2")
    assert ans is not None and ans.verified and ans.cost_min == 9.0
    assert ans.answer() == "B first at S1~S2: verified, +9.0 passenger delay-min"
    assert orch.planner.asked == [("plan-0", ("A", "B"), "B", "S1~S2")]


def test_a_forbidden_precedence_names_the_rule() -> None:
    bad = Candidate(
        name="contrast:B",
        plan_id="p",
        decisions=[],
        holds=[],
        g1=None,
        g2=None,
        g3=g3(0.0, fail=True),
    )
    c = Contrast(first="B", resource="S1~S2", candidate=bad, reversed=False, cost_min=None)
    assert not c.verified and c.answer() == "B first at S1~S2: not possible (VER-CON-01)"


# ---------------------------------------------------------------- on a real day
DAY = dt.date(2025, 8, 20)
START_S = 8 * 3600.0


@pytest.fixture(scope="module")
def looped() -> Orchestrator:
    world = run.load_world()
    dss = TwinDss(world, DAY, until_s=START_S + 3600)
    planner = TwinPlanner(world, DAY, cpsat_enabled=False, alternatives=0)
    loop = Orchestrator(world.register, dss, planner)
    loop.tick(START_S)
    return loop


def test_the_cycle_contrasts_its_most_urgent_decisions(looped: Orchestrator) -> None:
    cycle = looped.cycles[0]
    assert cycle.decisions and all(d.options >= 1 for d in cycle.decisions)
    assert cycle.contrasts  # opt.contrast.per_cycle decisions re-solved with the other precedence
    for c in cycle.contrasts:
        assert c.verified or c.fail  # a verified plan or the rules that forbid it
    for d in cycle.decisions:
        rev = [c for c in cycle.contrasts if c.resource == d.resource and c.reversed and c.verified]
        if rev:  # more than one feasible precedence: options with both
            assert d.options >= 2 and d.precedences >= 2


def test_why_not_on_the_twin(looped: Orchestrator) -> None:
    dec = looped.cycles[0].decisions[0]
    first = dec.trains[-1]
    ans = looped.why_not(dec.trains, first, dec.resource)
    assert ans is not None and ans.first == first
    assert ans.verified or ans.candidate.fail_rules
    assert ans.answer().startswith(f"{first} first at {dec.resource}")
