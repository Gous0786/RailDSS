"""The verifier's own plan representation and its results (spec §9.2, §9.4; FR-VER-03, FR-VER-11).

A candidate plan, a greedy plan, a controller modification or an executed ground-truth trace is
expressed in the same small IR, so one catalogue checks all of them (FR-VER-12). The IR is defined
here, inside the verifier, on purpose: the checker does not share the optimiser's or the twin's
data structures (NFR-MNT-02). Times are seconds since the plan origin (the start of the day).

A train's path is ``calls[0], runs[0], calls[1], runs[1], ..., calls[-1]``: ``runs[i]`` takes it
from ``calls[i]`` to ``calls[i + 1]``. Calls are at block stations; halts sit inside a run.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class StationCall(BaseModel):
    """A train on a station line: received at ``arr_s``, despatched (or leaves the section) at ``dep_s``."""

    op: str
    line_no: int
    arr_s: float
    dep_s: float
    stop: bool = False  # the train stands on the line (booked stop or held), so CSR applies
    booked_dep_s: float | None = None  # booked departure of a timetabled stop


class SubRun(BaseModel):
    """One automatic-signal sub-block of a run; ``seq`` counts in the travel order of the block id."""

    seq: int
    enter_s: float
    clear_s: float


class BlockRun(BaseModel):
    """A train on a block-section line: line clear / entry at ``enter_s``, head received at ``clear_s``."""

    block_id: str
    line_id: str
    enter_s: float
    clear_s: float
    sub_blocks: list[SubRun] = Field(default_factory=list)


class TrainPlan(BaseModel):
    run_id: str
    kind: Literal["passenger", "freight", "other"]
    length_m: float = Field(gt=0)
    electric: bool = True
    calls: list[StationCall]
    runs: list[BlockRun] = Field(default_factory=list)


class Action(BaseModel):
    """An explicit action in a plan, such as an order; ``kind`` is matched against the authority list."""

    run_id: str
    kind: str
    time_s: float
    note: str = ""


class MaintenanceBlock(BaseModel):
    """A maintenance or power block on a block section (``line_id`` None: every line)."""

    block_id: str
    line_id: str | None = None
    start_s: float
    end_s: float


class FrozenCall(BaseModel):
    """A frozen decision (commit window or line clear granted): this train keeps this line and departure."""

    run_id: str
    op: str
    line_no: int
    dep_s: float


class CandidatePlan(BaseModel):
    plan_id: str
    network_version: str
    trains: list[TrainPlan]
    actions: list[Action] = Field(default_factory=list)
    maintenance: list[MaintenanceBlock] = Field(default_factory=list)
    frozen: list[FrozenCall] = Field(default_factory=list)


# ------------------------------------------------------------------ results


class Severity(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    FAIL = "FAIL"

    @property
    def rank(self) -> int:
        return list(Severity).index(self)


class Status(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ERROR = "ERROR"


class Overall(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARN = "PASS_WITH_WARN"
    FAIL = "FAIL"


class Violation(BaseModel):
    trains: list[str]
    time_s: float
    detail: str


class Citation(BaseModel):
    type: str
    reference: str
    verification_status: str


class RuleResult(BaseModel):
    rule_id: str
    title: str
    status: Status
    declared_severity: Severity
    effective_severity: Severity
    citation: Citation
    violations: list[Violation] = Field(default_factory=list)
    error: str | None = None


class Report(BaseModel):
    """G2 result for one plan, persisted per recommendation (FR-VER-11)."""

    plan_id: str
    network_version: str
    catalogue_version: str
    register_version: str
    overall: Overall
    results: list[RuleResult]

    def by_rule(self) -> dict[str, RuleResult]:
        return {r.rule_id: r for r in self.results}

    def badge(self) -> str:
        """The UI badge text of spec §9.4."""
        applicable = [r for r in self.results if r.status is not Status.NOT_APPLICABLE]
        passed = sum(r.status is Status.PASS for r in applicable)
        if self.overall is Overall.FAIL:
            failed = [r.rule_id for r in applicable if r.status in (Status.FAIL, Status.ERROR)]
            return f"Failed: {', '.join(failed)}"
        if self.overall is Overall.PASS_WITH_WARN:
            return f"Verified with warnings ({passed}/{len(applicable)} rules)"
        return f"Verified ({passed}/{len(applicable)} rules)"
