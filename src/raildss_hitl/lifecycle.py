"""The recommendation lifecycle (spec §3.8; FR-HITL-01, FR-HITL-02, FR-HITL-07; issue #82).

This is the state machine model-checked in TLA+ (``specs/tla/HitlLifecycle.tla``, FR-VER-13): each
public method is one model action, named in its docstring, with the same guard and effect. The
conformance test replays every transition of the TLC state graph against this class.

* One recommendation covers one decision context. Presenting a new one supersedes the open one
  (PRESENTED or SANDBOX) of the same context; none is presented while one of that context is
  committed (accepted, in pre-execution check, or with orders out).
* A recommendation not acted on by ``valid_until`` expires; it can no longer be accepted.
* Accept is a controller decision; orders are issued only after the pre-execution check finds
  the plan current (planned on the latest state version) and no other plan holding a shared block.
* A material state change (:meth:`Lifecycle.state_changed`) makes every plan stale: plans in force
  diverge (a new cycle starts) and accepted ones fail their pre-execution check (SUPERSEDED).
"""

from __future__ import annotations

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum


class Status(StrEnum):
    PROPOSED = "PROPOSED"
    REJECTED_BY_VERIFIER = "REJECTED_BY_VERIFIER"
    PRESENTED = "PRESENTED"
    SANDBOX = "SANDBOX"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    PRE_EXEC_CHECK = "PRE_EXEC_CHECK"
    ORDER_ISSUED = "ORDER_ISSUED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    ORDER_DECLINED = "ORDER_DECLINED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    DIVERGED = "DIVERGED"


OPEN = frozenset({Status.PRESENTED, Status.SANDBOX})
COMMITTED = frozenset(
    {
        Status.ACCEPTED,
        Status.PRE_EXEC_CHECK,
        Status.ORDER_ISSUED,
        Status.ACKNOWLEDGED,
        Status.ORDER_DECLINED,
        Status.EXECUTING,
    }
)
AUTHORITY = frozenset({Status.ORDER_ISSUED, Status.ACKNOWLEDGED, Status.EXECUTING})
TERMINAL = frozenset(
    {
        Status.REJECTED_BY_VERIFIER,
        Status.REJECTED,
        Status.EXPIRED,
        Status.SUPERSEDED,
        Status.COMPLETED,
        Status.DIVERGED,
    }
)


class TransitionError(RuntimeError):
    """The action is not enabled for the recommendation in its current state."""


@dataclass
class Recommendation:
    rec_id: Hashable
    context: Hashable  # the decision context, e.g. "precedence 12704 / FRT-0417 at CPP"
    blocks: frozenset[Hashable]  # block sections its orders act on
    valid_until: float  # latest time to advise the station master
    based_on: int  # state version it was planned on
    status: Status = Status.PROPOSED
    decided: bool = False  # a controller decision (accept) stands behind it
    reason: str | None = None  # reject reason code
    history: list[tuple[float, Status]] = field(default_factory=list)


@dataclass
class Lifecycle:
    now: float = 0.0
    state_version: int = 0
    recs: dict[Hashable, Recommendation] = field(default_factory=dict)

    # ------------------------------------------------------------------ helpers
    def _get(self, rec_id: Hashable, *allowed: Status) -> Recommendation:
        rec = self.recs.get(rec_id)
        if rec is None:
            raise TransitionError(f"unknown recommendation {rec_id!r}")
        if rec.status not in allowed:
            raise TransitionError(f"{rec_id!r} is {rec.status}, not {'/'.join(allowed)}")
        return rec

    def _set(self, rec: Recommendation, status: Status) -> None:
        rec.status = status
        rec.history.append((self.now, status))

    def siblings(self, rec: Recommendation) -> list[Recommendation]:
        return [r for r in self.recs.values() if r is not rec and r.context == rec.context]

    def rivals(self, rec: Recommendation) -> list[Recommendation]:
        return [r for r in self.recs.values() if r is not rec and r.blocks & rec.blocks]

    def stale(self, rec: Recommendation) -> bool:
        return rec.based_on != self.state_version

    # ------------------------------------------------------------------ environment
    def advance(self, now: float) -> None:
        """Tick: time moves forward."""
        if now < self.now:
            raise TransitionError(f"time cannot go back from {self.now} to {now}")
        self.now = now

    def state_changed(self) -> None:
        """StateChange: a material change of the estimated state; plans in force diverge."""
        self.state_version += 1
        for rec in self.recs.values():
            if rec.status in AUTHORITY:
                self._set(rec, Status.DIVERGED)

    # ------------------------------------------------------------------ actions
    def propose(
        self, rec_id: Hashable, context: Hashable, blocks: Iterable[Hashable], valid_until: float
    ) -> Recommendation:
        """Propose: a new candidate for ``context``, planned on the current state."""
        if rec_id in self.recs:
            raise TransitionError(f"{rec_id!r} exists")
        rec = Recommendation(rec_id, context, frozenset(blocks), valid_until, self.state_version)
        rec.history.append((self.now, rec.status))
        self.recs[rec_id] = rec
        return rec

    def verification_result(self, rec_id: Hashable, passed: bool) -> None:
        """Present (PASS / PASS_WITH_WARN) or VerifyFail (any gate FAIL, never shown as executable)."""
        rec = self._get(rec_id, Status.PROPOSED)
        if not passed:
            self._set(rec, Status.REJECTED_BY_VERIFIER)
            return
        if any(s.status in COMMITTED for s in self.siblings(rec)):
            raise TransitionError(f"context {rec.context!r} has a committed recommendation")
        for s in self.siblings(rec):
            if s.status in OPEN:
                self._set(s, Status.SUPERSEDED)
        self._set(rec, Status.PRESENTED)

    def accept(self, rec_id: Hashable) -> None:
        """Accept: the controller's decision, only before ``valid_until``."""
        rec = self._get(rec_id, Status.PRESENTED)
        if self.now >= rec.valid_until:
            raise TransitionError(f"{rec_id!r} is past valid_until {rec.valid_until}")
        rec.decided = True
        self._set(rec, Status.ACCEPTED)

    def modify(self, rec_id: Hashable) -> None:
        """Modify: open the sandbox with the option preloaded."""
        self._set(self._get(rec_id, Status.PRESENTED), Status.SANDBOX)

    def reverified(self, rec_id: Hashable, passed: bool) -> None:
        """ReverifyPass (back to PRESENTED); a FAIL keeps the sandbox open (stutter)."""
        rec = self._get(rec_id, Status.SANDBOX)
        if passed:
            self._set(rec, Status.PRESENTED)

    def reject(self, rec_id: Hashable, reason_code: str) -> None:
        """Reject: needs a reason code (FR-HITL-05)."""
        if not reason_code:
            raise TransitionError("a reject needs a reason code")
        rec = self._get(rec_id, Status.PRESENTED)
        rec.reason = reason_code
        self._set(rec, Status.REJECTED)

    def expire(self, rec_id: Hashable) -> None:
        """Expire: not acted on by ``valid_until`` (FR-HITL-07)."""
        rec = self._get(rec_id, Status.PRESENTED)
        if self.now < rec.valid_until:
            raise TransitionError(f"{rec_id!r} is valid until {rec.valid_until}")
        self._set(rec, Status.EXPIRED)

    def expire_due(self) -> list[Hashable]:
        """The timer: expire every presented recommendation past its ``valid_until``."""
        due = [
            r.rec_id for r in self.recs.values() if r.status is Status.PRESENTED and self.now >= r.valid_until
        ]
        for rec_id in due:
            self.expire(rec_id)
        return due

    def pre_exec_check(self, rec_id: Hashable) -> None:
        """PreExecCheck: the executor re-validates an accepted plan against the latest state."""
        self._set(self._get(rec_id, Status.ACCEPTED), Status.PRE_EXEC_CHECK)

    def pre_exec_result(self, rec_id: Hashable) -> Status:
        """PreExecOk (orders issued) or PreExecFail (stale or a shared block is held: SUPERSEDED)."""
        rec = self._get(rec_id, Status.PRE_EXEC_CHECK)
        held = any(r.status in AUTHORITY for r in self.rivals(rec))
        self._set(rec, Status.SUPERSEDED if self.stale(rec) or held else Status.ORDER_ISSUED)
        return rec.status

    def acknowledged(self, rec_id: Hashable) -> None:
        """Acknowledge: all station masters acknowledged the orders."""
        self._set(self._get(rec_id, Status.ORDER_ISSUED), Status.ACKNOWLEDGED)

    def declined(self, rec_id: Hashable) -> None:
        """Decline: a station master declined, or the acknowledgement timed out."""
        self._set(self._get(rec_id, Status.ORDER_ISSUED), Status.ORDER_DECLINED)

    def replan(self, rec_id: Hashable, valid_until: float) -> None:
        """Replan: a new plan with the declined constraint, presented for a new decision."""
        rec = self._get(rec_id, Status.ORDER_DECLINED)
        rec.based_on, rec.valid_until, rec.decided = self.state_version, valid_until, False
        self._set(rec, Status.PRESENTED)

    def start(self, rec_id: Hashable) -> None:
        """Start: execution begins."""
        self._set(self._get(rec_id, Status.ACKNOWLEDGED), Status.EXECUTING)

    def completed(self, rec_id: Hashable) -> None:
        """Complete: all actions observed."""
        self._set(self._get(rec_id, Status.EXECUTING), Status.COMPLETED)

    def diverged(self, rec_id: Hashable) -> None:
        """Diverge: the observed state departs from the plan beyond tolerance (new cycle)."""
        self._set(self._get(rec_id, Status.EXECUTING), Status.DIVERGED)
