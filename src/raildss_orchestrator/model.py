"""What the loop plans with and verifies with (issue #84)."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from raildss_hitl.executor import SECONDS_PER_DAY, SECONDS_PER_HOUR, SECONDS_PER_MIN, PlanAction
from raildss_optimiser.greedy import Decision, Hold
from raildss_twin.sandbox import G3Result
from raildss_twin.sim import Closure
from raildss_verifier.gates import GateResult, StateSnapshot
from raildss_verifier.model import Report, Status

MAX_OPTIONS = 3  # FR-OPT-07: at most three verified options per decision, plus the baseline
KPI_RANK = "passenger_arrival_delay_min"


class ConflictLike(Protocol):
    type: int
    trains: tuple[str, ...]
    resource: str
    time: dt.datetime
    priority: str
    near: bool

    @property
    def key(self) -> tuple[Any, ...]: ...


@dataclass
class DssView:
    """What the DSS chain knows at one tick: ``S_t`` (as G0 sees it) and the conflicts it predicts."""

    now_s: float
    version: int  # estimator version: changes only on a material state change
    snapshot: StateSnapshot
    known_trains: frozenset[str]
    conflicts: list[ConflictLike]
    state_hash: str = ""  # hash of the estimated state S_t (FR-PRED-02)
    alerts: list[dict[str, Any]] = field(default_factory=list)  # data-quality alerts new at this tick


@dataclass
class PlanContext:
    now_s: float
    plan_id: str
    triggers: list[str]
    frozen: list[Decision]
    committed: list[Decision]  # accepted orders beyond the commit window
    impose: bool  # the world changed (closures): impose the frozen decisions; else only check them
    history: list[Decision]  # decisions that have started: fixed, as they happened
    closures: list[Closure]
    exit_windows: Mapping[str, list[tuple[float, float]]]
    commit_holds: Mapping[tuple[str, str], float]  # accepted hold orders in force
    commit_lines: Mapping[tuple[str, str], int]  # accepted line orders in force
    policy_from_s: float
    verify: bool  # build and verify the options (False: only the greedy plan that carries the rolling state)
    time_limit_s: float
    previous: list[Decision] = field(default_factory=list)  # the last plan: kept by the stability term
    incidents: list[Any] = field(default_factory=list)  # announced incidents the plan models (issue #155)
    entry_delays: dict[str, float] = field(default_factory=dict)  # known late entries: run id -> s late
    # accepted short-terminations and cancellations in force: run id -> station (issue #156)
    commit_terminations: dict[str, str] = field(default_factory=dict)
    # the decisions to contrast (issue #154): (trains, resource) of the most urgent conflicts released
    decisions: list[tuple[tuple[str, ...], str]] = field(default_factory=list)


@dataclass
class Candidate:
    """One whole-plan option, with the verdict of every gate."""

    name: str  # greedy | cpsat | cpsat-alt1 ...
    plan_id: str
    decisions: list[Decision]
    holds: list[Hold]
    g1: GateResult | None
    g2: Report | None  # the rule catalogue on the plan's own trace (greedy); CP-SAT: inside G3
    g3: G3Result
    solver: dict[str, Any] = field(default_factory=dict)
    # disruption options (issue #156): trains it short-terminates (or cancels at their first station),
    # run id -> station, each ranked with ``penalty_min`` passenger delay-minutes (opt.disruption)
    terminations: dict[str, str] = field(default_factory=dict)
    cancelled: frozenset[str] = frozenset()
    penalty_min: float = 0.0

    @property
    def fail_rules(self) -> list[str]:
        out = [f"G1:{f.check}" for f in self.g1.findings] if self.g1 else []
        if self.g2 is not None:
            out += [r.rule_id for r in self.g2.results if r.status in (Status.FAIL, Status.ERROR)]
        out += [v.rule_id for v in self.g3.violations]
        return sorted(set(out))

    @property
    def warn_rules(self) -> list[str]:
        out = [r.rule_id for r in self.g2.results if r.status is Status.WARN] if self.g2 else []
        if self.g3.status == "PASS_WITH_WARN":
            out.append("G3:WARN")
        return sorted(set(out))

    @property
    def verified(self) -> bool:
        return not self.fail_rules

    @property
    def delta(self) -> dict[str, float]:
        out = {k: float(v) for k, v in self.g3.kpis["delta"].items()}
        if self.terminations:  # a short-termination or cancellation costs its penalty (FR-OPT-11)
            out[KPI_RANK] = out.get(KPI_RANK, 0.0) + self.penalty_min * len(self.terminations)
        return out

    def actions_for(self, trains: Sequence[str], resource: str) -> list[PlanAction]:
        """The orders this plan gives for one conflict: holds of its trains at the resource's stations,
        and for a station conflict the line each train is received on; a short-termination or cancellation of
        one of its trains comes first."""
        ends = [
            PlanAction(kind="cancel" if r in self.cancelled else "short_terminate", run_id=r, station=s)
            for r, s in sorted(self.terminations.items())
            if r in trains
        ]
        return ends + [
            a
            for a in actions_for(self.decisions, self.holds, trains, resource)
            if a.run_id not in self.terminations
        ]


def order_of(decisions: Sequence[Decision], trains: Sequence[str], resource: str) -> list[str]:
    """The trains of a decision in the order a plan serves them at ``resource`` (issue #154): by their
    departure from the resource's stations; a train with none there comes last."""
    stations = set(resource.split("~"))
    dep: dict[str, float] = {}
    for d in decisions:
        if d.run_id in trains and d.station in stations:
            dep[d.run_id] = min(dep.get(d.run_id, math.inf), d.dep_s)
    return sorted(trains, key=lambda t: (dep.get(t, math.inf), t))


def actions_for(
    decisions: Sequence[Decision], holds: Sequence[Hold], trains: Sequence[str], resource: str
) -> list[PlanAction]:
    """The orders a plan gives for one conflict (also used to replay a recommendation from its record)."""
    stations = set(resource.split("~"))
    out = [
        PlanAction(kind="hold_until", run_id=h.run_id, station=h.station, until_s=h.until_s)
        for h in holds
        if h.run_id in trains and h.station in stations
    ]
    if "~" not in resource:
        out += [
            PlanAction(kind="receive_on_line", run_id=d.run_id, station=d.station, line_no=d.line_no)
            for d in decisions
            if d.run_id in trains and d.station == resource
        ]
    return out


@dataclass
class Contrast:
    """'Why not X' (issue #154, FR-OPT-06): the plan with ``first`` served before the other trains of a
    decision at ``resource``, verified, and its cost against the best verified plan of the cycle."""

    first: str
    resource: str
    candidate: Candidate
    reversed: bool  # the precedence at the resource is the other way round from the best plan's
    cost_min: float | None  # its KPI_RANK delta minus the best plan's (positive: it costs more)

    @property
    def verified(self) -> bool:
        return self.candidate.verified

    def answer(self) -> str:
        if not self.verified:
            rules = ", ".join(self.candidate.fail_rules)
            return f"{self.first} first at {self.resource}: not possible ({rules})"
        if not self.reversed:
            return f"{self.first} first at {self.resource}: the plan keeps the same order"
        return f"{self.first} first at {self.resource}: verified, {self.cost_min:+.1f} passenger delay-min"


@dataclass
class Contingent:
    """FR-OPT-11: the plan for one ETR scenario of the incidents whose restoration time is uncertain, and
    its switch rule: if they are not restored by ``switch_at_s`` (the decision time), switch to this plan."""

    etr_s: float  # the restoration time this plan assumes
    switch_at_s: float
    candidate: Candidate

    def rule(self) -> str:
        return (
            f"if not restored by {_hhmm(self.switch_at_s)}, switch to the plan for ETR {_hhmm(self.etr_s)}"
            f" ({self.candidate.name}, {'verified' if self.candidate.verified else 'not verified'})"
        )


def _hhmm(s: float) -> str:
    t = int(s) % SECONDS_PER_DAY
    return f"{t // SECONDS_PER_HOUR:02d}:{t % SECONDS_PER_HOUR // SECONDS_PER_MIN:02d}"


@dataclass
class PlanSet:
    decisions: list[Decision]  # the rolling plan (greedy): what the next plan freezes
    candidates: list[Candidate] = field(default_factory=list)
    baseline: dict[str, float] = field(default_factory=dict)  # BL-0 window KPIs: the baseline option
    runtime_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    planner: str = "greedy"  # which plan carries the rolling state: greedy | cpsat (issue #152)
    churn: int | None = None  # decisions of the previous plan changed by this one (opt.stability.churn_s)
    contingent: list[Contingent] = field(default_factory=list)  # ETR scenarios (issue #156)
    contrasts: list[Contrast] = field(default_factory=list)  # the other precedence of decisions (#154)
    first_plan_s: float | None = None  # wall time to the first verified option (DSC-06)


class Dss(Protocol):
    def advance(self, now_s: float) -> DssView: ...

    def commit(
        self,
        holds: Mapping[tuple[str, str], float],
        lines: Mapping[tuple[str, str], int],
        terminations: Mapping[str, str] | None = None,
    ) -> None:
        """Accepted orders change the world the feed observes from now on."""


class Planner(Protocol):
    def plan(self, ctx: PlanContext) -> PlanSet: ...
