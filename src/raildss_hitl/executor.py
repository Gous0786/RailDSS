"""Action executor: control orders, acknowledgements and re-planning (spec FR-HITL-03, -05, -06, -08,
-09, -10; FR-VER-07..09; issue #83).

The :class:`Executor` drives the recommendation :class:`~raildss_hitl.lifecycle.Lifecycle` (#82):

* **Present.** An option is verified (the release policy: the ``verify`` callback runs the gates and
  returns their FAIL and WARN rule ids) and presented only if nothing FAILs.
* **Accept** needs a reason when the option carries WARNs (FR-HITL-06). The pre-execution check
  (FR-VER-09) re-verifies against the latest state. A stale plan or a FAIL makes it SUPERSEDED and no
  order is issued. Otherwise every action becomes a per-station control order in IR phrasing
  (FR-HITL-03), published as a ``CONTROL_ORDER`` on ``ctrl.orders``.
* **Feedback.** ``ORDER_ACK`` events from the station masters (``ctrl.order_acks``) update each order. When
  all are acknowledged the recommendation is ACKNOWLEDGED and then EXECUTING (FR-HITL-08). A decline
  makes it ORDER_DECLINED, raises a P2 alert and **re-plans** with the declined action as a constraint
  (FR-HITL-09). The new option is verified and re-presented, and its latency is kept against the Tier-1
  budget.
* **Reject** needs a reason code (FR-HITL-05). **Manual orders** are verified like any plan and blocked
  with the failing rule ids (FR-HITL-10). **Auto-accept** is for headless evaluation runs only.

The executor talks to the station masters only through events (``publish`` and :meth:`Executor.on_ack`),
so it works the same in process, in the twin and over Kafka.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable, Hashable, Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from raildss_hitl.lifecycle import Lifecycle, Status, TransitionError

REJECT_REASONS = ("UNSAFE_LOCAL", "CREW_LOCO", "PRIORITY_INSTRUCTION", "DATA_WRONG", "OTHER")  # FR-HITL-05
SECONDS_PER_DAY = 24 * 3600
SECONDS_PER_HOUR = 3600
SECONDS_PER_MIN = 60


class PlanAction(BaseModel):
    kind: Literal["receive_on_line", "hold_until"]
    run_id: str
    station: str
    line_no: int | None = None
    until_s: float | None = None

    def phrase(self) -> str:
        """The order in IR control phrasing (FR-HITL-03)."""
        if self.kind == "receive_on_line":
            return f"SM/{self.station}: receive {self.run_id} on line {self.line_no}"
        t = int(self.until_s or 0) % SECONDS_PER_DAY
        hhmm = f"{t // SECONDS_PER_HOUR:02d}:{t % SECONDS_PER_HOUR // SECONDS_PER_MIN:02d}"
        return f"SM/{self.station}: hold {self.run_id} until {hhmm}"


class Option(BaseModel):
    option_id: str
    actions: list[PlanAction]
    authored_by: Literal["optimiser", "greedy", "controller"] = "optimiser"


class Verdict(BaseModel):
    """What the release policy says about an option: G0-G2 FAIL rule ids (and G3), WARN rule ids."""

    fail: list[str] = Field(default_factory=list)
    warn: list[str] = Field(default_factory=list)

    @property
    def releasable(self) -> bool:
        return not self.fail


class OrderRecord(BaseModel):
    order_id: str
    rec_id: str
    action: PlanAction
    text: str
    status: Literal["ISSUED", "ACKNOWLEDGED", "DECLINED"] = "ISSUED"
    reason_code: str | None = None
    issued_s: float
    answered_s: float | None = None


class Alert(BaseModel):
    priority: Literal["P1", "P2", "P3"]
    time_s: float
    text: str


class ReplanRecord(BaseModel):
    rec_id: str
    declined: list[PlanAction]
    option_id: str | None
    verdict: Verdict | None
    wall_s: float  # time to a new verified option (Tier-1 budget, NFR-PERF-01)


class BlockedOrderError(RuntimeError):
    """A manual order that breaks a rule (FR-HITL-10)."""

    def __init__(self, rules: list[str]) -> None:
        super().__init__(f"blocked by {', '.join(rules)}")
        self.rules = rules


Verify = Callable[[Option], Verdict]
Replan = Callable[[Option, list[PlanAction]], Option | None]
Publish = Callable[[str, dict[str, Any]], None]


class Executor:
    def __init__(
        self,
        lifecycle: Lifecycle,
        publish: Publish,
        verify: Verify,
        replan: Replan,
        now: Callable[[], float],
        *,
        validity_s: float,
        auto_accept: bool = False,
        evaluation_mode: bool = False,
    ) -> None:
        if auto_accept and not evaluation_mode:
            raise ValueError("auto-accept is only for headless evaluation runs (FR-HITL)")
        self.lc, self.publish, self.verify, self.replanner, self.now = lifecycle, publish, verify, replan, now
        self.validity_s = validity_s
        self.auto_accept = auto_accept
        self.options: dict[str, Option] = {}
        self.verdicts: dict[str, Verdict] = {}
        self.orders: dict[str, OrderRecord] = {}
        self.current: dict[str, list[str]] = {}  # recommendation -> order ids of its plan in force
        self.alerts: list[Alert] = []
        self.replans: list[ReplanRecord] = []
        self.log: list[dict[str, Any]] = []
        self._ids = itertools.count(1)

    # ------------------------------------------------------------------ presenting
    def present(
        self, rec_id: str, context: Hashable, option: Option, blocks: Iterable[Hashable] = ()
    ) -> Verdict:
        """Propose, verify and (if releasable) present an option for a decision context."""
        self.lc.advance(self.now())
        stations = {a.station for a in option.actions}
        self.lc.propose(rec_id, context, set(blocks) or stations, self.now() + self.validity_s)
        verdict = self.verify(option)
        self.options[rec_id], self.verdicts[rec_id] = option, verdict
        self.lc.verification_result(rec_id, verdict.releasable)
        self._log("present", rec_id, option=option.option_id, fail=verdict.fail, warn=verdict.warn)
        if verdict.releasable and self.auto_accept:
            self.accept(rec_id, warn_reason="auto-accept (evaluation)" if verdict.warn else None)
        return verdict

    def state_changed(self) -> None:
        """A new estimated state ``S_t`` that invalidates plans made on the previous one."""
        self.lc.advance(self.now())
        self.lc.state_changed()

    # ------------------------------------------------------------------ decisions
    def accept(self, rec_id: str, warn_reason: str | None = None) -> Status:
        """Accept (FR-HITL-03/06), then the pre-execution check (FR-VER-09) and the orders."""
        verdict = self.verdicts[rec_id]
        if verdict.warn and not warn_reason:
            raise TransitionError(f"{rec_id} has warnings {verdict.warn}: a reason is required to override")
        self.lc.advance(self.now())
        self.lc.accept(rec_id)
        self._log("accept", rec_id, warn_reason=warn_reason)
        self.lc.pre_exec_check(rec_id)
        recheck = self.verify(self.options[rec_id])
        if not recheck.releasable and not self.lc.stale(self.lc.recs[rec_id]):
            # the latest state no longer supports the plan: a material change for the lifecycle (#82)
            self.lc.state_changed()
        status = self.lc.pre_exec_result(rec_id)
        if status is Status.ORDER_ISSUED:
            self.current[rec_id] = [self._issue(rec_id, a).order_id for a in self.options[rec_id].actions]
        else:
            self._log("superseded", rec_id, fail=recheck.fail)
        return status

    def reject(self, rec_id: str, reason_code: str, note: str = "") -> None:
        """Reject with a reason code (FR-HITL-05); OTHER needs a note."""
        if reason_code not in REJECT_REASONS:
            raise TransitionError(f"reason code must be one of {REJECT_REASONS}")
        if reason_code == "OTHER" and not note:
            raise TransitionError("reason OTHER needs a note")
        self.lc.reject(rec_id, reason_code)
        self._log("reject", rec_id, reason=reason_code, note=note)

    def manual(self, actions: list[PlanAction], controller: str) -> list[OrderRecord]:
        """A controller-authored order, verified like any plan (FR-HITL-10)."""
        option = Option(option_id=f"MANUAL-{next(self._ids)}", actions=actions, authored_by="controller")
        verdict = self.verify(option)
        self._log("manual", option.option_id, controller=controller, fail=verdict.fail)
        if not verdict.releasable:
            raise BlockedOrderError(verdict.fail)
        return [self._issue(option.option_id, a) for a in actions]

    # ------------------------------------------------------------------ orders and feedback
    def _issue(self, rec_id: str, action: PlanAction) -> OrderRecord:
        order_id = f"ORD-{next(self._ids):05d}"
        rec = OrderRecord(
            order_id=order_id, rec_id=rec_id, action=action, text=action.phrase(), issued_s=self.now()
        )
        self.orders[order_id] = rec
        payload = {
            "order_id": order_id,
            "station": action.station,
            "run_id": action.run_id,
            "action": action.kind,
            "line_no": action.line_no,
            "until_s": action.until_s,
            "text": rec.text,
            "recommendation_id": rec_id,
        }
        self.publish("ctrl.orders", payload)
        self._log("order", rec_id, order=order_id, text=rec.text)
        return rec

    def on_ack(self, payload: dict[str, Any]) -> None:
        """An ``ORDER_ACK`` from ``ctrl.order_acks``."""
        rec = self.orders.get(str(payload.get("order_id")))
        if rec is None or rec.status != "ISSUED":
            return
        rec.status = "ACKNOWLEDGED" if payload.get("status") == "ACKNOWLEDGED" else "DECLINED"
        rec.reason_code = payload.get("reason_code")
        rec.answered_s = self.now()
        if rec.rec_id not in self.lc.recs:
            return  # a manual order: tracked, no lifecycle
        lc_rec = self.lc.recs[rec.rec_id]
        if lc_rec.status is not Status.ORDER_ISSUED:
            return
        self.lc.advance(self.now())
        if rec.status == "DECLINED":
            self.lc.declined(rec.rec_id)
            self.alerts.append(
                Alert(priority="P2", time_s=self.now(), text=f"{rec.text} declined: {rec.reason_code}")
            )
            self._replan(rec.rec_id)
            return
        if all(self.orders[oid].status == "ACKNOWLEDGED" for oid in self.current.get(rec.rec_id, [])):
            self.lc.acknowledged(rec.rec_id)
            self.lc.start(rec.rec_id)

    def _replan(self, rec_id: str) -> None:
        """Re-plan with the declined actions as constraints (FR-HITL-09) and re-present."""
        t0 = time.perf_counter()
        declined = [o.action for o in self.orders.values() if o.rec_id == rec_id and o.status == "DECLINED"]
        new = self.replanner(self.options[rec_id], declined)
        verdict = self.verify(new) if new is not None else None
        wall = time.perf_counter() - t0
        self.replans.append(
            ReplanRecord(
                rec_id=rec_id,
                declined=declined,
                option_id=new.option_id if new else None,
                verdict=verdict,
                wall_s=round(wall, 4),
            )
        )
        if new is None or verdict is None or not verdict.releasable:
            self.alerts.append(Alert(priority="P2", time_s=self.now(), text=f"{rec_id}: no verified re-plan"))
            return
        for o in self.orders.values():  # the old orders are withdrawn with the old plan
            if o.rec_id == rec_id and o.status == "ISSUED":
                o.status = "DECLINED"
                o.reason_code = "WITHDRAWN"
        self.options[rec_id], self.verdicts[rec_id] = new, verdict
        self.lc.replan(rec_id, self.now() + self.validity_s)
        self._log("replan", rec_id, option=new.option_id, wall_s=round(wall, 4))
        if self.auto_accept:
            self.accept(rec_id, warn_reason="auto-accept (evaluation)" if verdict.warn else None)

    def _log(self, what: str, rec_id: str, **detail: Any) -> None:
        self.log.append({"time_s": self.now(), "event": what, "rec_id": rec_id, **detail})
