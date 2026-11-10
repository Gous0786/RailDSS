"""Station-master agents (spec FR-TWIN-15; issue #53).

:class:`StationMasters` take control orders (the ``CONTROL_ORDER`` events of ``ctrl.orders``),
answer each after a seeded latency drawn from ``hitl.sm_ack_latency_s`` (30-90 s), and either:

* **decline** an order that cannot be carried out on the ground, with a reason code:
  ``LOOP_OCCUPIED`` (the line is held by a train or out of use), ``RECEPTION_NOT_PERMITTED`` (no
  Connections edge from the block line the train comes by, or no such line), ``LOOP_TOO_SHORT``
  (the line's CSR is shorter than the train), ``TRAIN_PASSED`` (the train is already beyond the
  station) or ``UNKNOWN_TRAIN``; or
* **acknowledge** it and apply it in the twin (a :class:`~raildss_twin.sim.Directives` hold or line
  choice, which the movement simulator then carries out under its rules).

Each answer is an ``ORDER_ACK`` event on ``ctrl.order_acks`` with the order id, status, reason code
and latency. Orders reach the agents through :meth:`StationMasters.receive` (thread-safe: an
in-process DSS, the twin console, or a ``ctrl.orders`` consumer via :meth:`StationMasters.on_event`).
"""

from __future__ import annotations

import queue
import random
from collections.abc import Generator
from typing import Any, Literal

from pydantic import BaseModel

from raildss_common.envelope import EventEnvelope, EventType
from raildss_twin.sim import Sim, Train, token

Proc = Generator[Any, Any, Any]
SM_SOURCE = "SIM.SM"
TOPIC = "ctrl.order_acks"

Reason = Literal[
    "LOOP_OCCUPIED", "RECEPTION_NOT_PERMITTED", "LOOP_TOO_SHORT", "TRAIN_PASSED", "UNKNOWN_TRAIN"
]


class Order(BaseModel):
    order_id: str
    station: str
    run_id: str
    action: Literal["receive_on_line", "hold_until"]
    line_no: int | None = None
    until_s: float | None = None


class Answer(BaseModel):
    order_id: str
    station: str
    run_id: str
    status: Literal["ACKNOWLEDGED", "DECLINED"]
    reason_code: Reason | None = None
    received_s: float
    answered_s: float

    @property
    def latency_s(self) -> float:
        return self.answered_s - self.received_s


class StationMasters:
    def __init__(self, sim: Sim, seed: int | None = None) -> None:
        self.sim = sim
        latency = sim.register.value("hitl.sm_ack_latency_s")
        self.lo, self.hi = float(latency["min"]), float(latency["max"])
        self.rng = random.Random(sim.seed if seed is None else seed)
        self.pending: queue.Queue[Order] = queue.Queue()
        self.answers: list[Answer] = []
        sim.env.process(self._poll())

    # ------------------------------------------------------------------ intake
    def receive(self, order: Order) -> None:
        """Thread-safe intake; the order is picked up within one state period of simulated time."""
        self.pending.put(order)

    def submit(self, order: Order) -> None:
        """Take an order now (from inside the simulation, or before it runs)."""
        self.sim.env.process(self._handle(order))

    def on_event(self, event: EventEnvelope) -> None:
        """A ``CONTROL_ORDER`` event from ``ctrl.orders``."""
        if event.event_type is EventType.CONTROL_ORDER:
            self.receive(Order.model_validate(event.payload))

    def _poll(self) -> Proc:
        while True:
            yield self.sim.env.timeout(self.sim.state_period)
            while not self.pending.empty():
                self.submit(self.pending.get())

    # ------------------------------------------------------------------ decision
    def _train(self, run_id: str) -> Train | None:
        return next((t for t in self.sim.trains if run_id in (t.plan.run_id, token(t.plan.run_id))), None)

    def _passed(self, t: Train, station: str, receiving: bool) -> bool:
        ops = [n.op for n in t.plan.nodes]
        if station not in ops or t.status == "done":
            return True
        if not t.entered or not t.op:
            return False
        here, there = ops.index(t.op), ops.index(station)
        if there != here:
            return there < here
        # at the station itself: too late to receive it elsewhere once received; a hold still works
        received = any(label.startswith(f"SL:{station}/") for label in t.held) and t.block is None
        return (receiving and received) or t.block is not None

    def decide(self, order: Order) -> Reason | None:
        t = self._train(order.run_id)
        if t is None:
            return "UNKNOWN_TRAIN"
        if self._passed(t, order.station, order.action == "receive_on_line"):
            return "TRAIN_PASSED"
        if order.action == "hold_until":
            return None
        layout = self.sim.layout
        line = next(
            (sl for sl in layout.station_lines.get(order.station, []) if sl.line_no == order.line_no), None
        )
        if line is None or line.status != "in_use":
            return "RECEPTION_NOT_PERMITTED"
        stations = [n.op for n in t.plan.nodes if not layout.ops[n.op].is_halt]
        k = stations.index(order.station) if order.station in stations else 0
        if k > 0:  # it arrives by a block line: Connections must let this line receive from it
            prev = stations[k - 1]
            block = layout.block_by_pair.get((prev, order.station))
            permits = layout.receive.get((order.station, line.line_no), set())
            ok = block is not None and any(
                (block.id, ln.line_id) in permits
                for ln in layout.lines_for(block.id, layout.direction(prev, order.station))
            )
            if not ok:
                return "RECEPTION_NOT_PERMITTED"
        if line.csr_m is not None and line.csr_m < t.plan.template.length_m:
            return "LOOP_TOO_SHORT"
        res = self.sim.resources.get(f"SL:{order.station}/{line.line_no}")
        if res is not None and res.count >= res.capacity:
            return "LOOP_OCCUPIED"
        return None

    def _handle(self, order: Order) -> Proc:
        received = self.sim.env.now
        yield self.sim.env.timeout(self.rng.uniform(self.lo, self.hi))
        reason = self.decide(order)
        t = self._train(order.run_id)
        if reason is None and t is not None:
            key = (t.plan.run_id, order.station)
            if order.action == "hold_until" and order.until_s is not None:
                self.sim.directives.holds[key] = order.until_s
            elif order.line_no is not None:
                self.sim.directives.lines[key] = order.line_no
        answer = Answer(
            order_id=order.order_id,
            station=order.station,
            run_id=order.run_id,
            status="DECLINED" if reason else "ACKNOWLEDGED",
            reason_code=reason,
            received_s=received,
            answered_s=self.sim.env.now,
        )
        self.answers.append(answer)
        self.sim.emit(
            TOPIC,
            EventType.ORDER_ACK,
            t,
            {
                "order_id": order.order_id,
                "station": order.station,
                "status": answer.status,
                "reason_code": reason,
                "latency_s": round(answer.latency_s, 1),
            },
            self.sim.km_of(order.station),
            order.station,
            source=SM_SOURCE,
        )
