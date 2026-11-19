"""What the loop plans with and verifies with (issue #84)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from raildss_hitl.executor import PlanAction
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
        return {k: float(v) for k, v in self.g3.kpis["delta"].items()}

    def actions_for(self, trains: Sequence[str], resource: str) -> list[PlanAction]:
        """The orders this plan gives for one conflict: holds of its trains at the resource's stations,
        and for a station conflict the line each train is received on."""
        return actions_for(self.decisions, self.holds, trains, resource)


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
class PlanSet:
    decisions: list[Decision]  # the rolling plan (greedy): what the next plan freezes
    candidates: list[Candidate] = field(default_factory=list)
    baseline: dict[str, float] = field(default_factory=dict)  # BL-0 window KPIs: the baseline option
    runtime_s: float = 0.0
    notes: list[str] = field(default_factory=list)


class Dss(Protocol):
    def advance(self, now_s: float) -> DssView: ...

    def commit(self, holds: Mapping[tuple[str, str], float], lines: Mapping[tuple[str, str], int]) -> None:
        """Accepted orders change the world the feed observes from now on."""


class Planner(Protocol):
    def plan(self, ctx: PlanContext) -> PlanSet: ...
