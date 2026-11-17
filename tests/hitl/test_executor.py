"""Action executor: orders, acknowledgements, declines and re-planning (FR-HITL-03..10, FR-VER-09; #83)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope
from raildss_hitl.executor import BlockedOrderError, Executor, Option, PlanAction, Verdict
from raildss_hitl.lifecycle import Lifecycle, Status, TransitionError
from raildss_twin import run
from raildss_twin.agents import Order, StationMasters
from raildss_twin.injector import Disruption, Injector
from raildss_twin.sim import Sim

DAY = dt.date(2025, 8, 20)
T0 = 10 * 3600
TIER1_BUDGET_S = 10.0  # NFR-PERF-01 P95


def _executor(verdicts: list[Verdict], **kw: Any) -> tuple[Executor, list[dict[str, Any]]]:
    sent: list[dict[str, Any]] = []
    queue = list(verdicts)
    ex = Executor(
        Lifecycle(),
        lambda topic, payload: sent.append(payload),
        lambda option: queue.pop(0) if queue else Verdict(),
        lambda option, declined: None,
        lambda: 0.0,
        validity_s=600,
        **kw,
    )
    return ex, sent


A = PlanAction(kind="receive_on_line", run_id="12863", station="CPP", line_no=3)


def test_accepting_an_option_with_warnings_needs_a_reason() -> None:
    ex, sent = _executor([Verdict(warn=["VER-BUF-01"]), Verdict(warn=["VER-BUF-01"])])
    ex.present("R-1", "ctx", Option(option_id="A", actions=[A]))
    with pytest.raises(TransitionError, match="reason"):
        ex.accept("R-1")
    assert ex.accept("R-1", warn_reason="buffer accepted, line clear") is Status.ORDER_ISSUED
    assert sent[0]["text"] == "SM/CPP: receive 12863 on line 3"


def test_reject_needs_a_reason_code_and_other_needs_a_note() -> None:
    ex, _ = _executor([])
    ex.present("R-1", "ctx", Option(option_id="A", actions=[A]))
    with pytest.raises(TransitionError):
        ex.reject("R-1", "")
    with pytest.raises(TransitionError, match="note"):
        ex.reject("R-1", "OTHER")
    ex.reject("R-1", "UNSAFE_LOCAL")
    assert ex.lc.recs["R-1"].status is Status.REJECTED


def test_a_plan_that_fails_the_pre_execution_check_is_superseded_without_orders() -> None:
    ex, sent = _executor([Verdict(), Verdict(fail=["VER-XNG-02"])])
    ex.present("R-1", "ctx", Option(option_id="A", actions=[A]))
    assert ex.accept("R-1") is Status.SUPERSEDED
    assert sent == []


def test_a_manual_order_that_breaks_a_rule_is_blocked_with_the_rule_id() -> None:
    ex, sent = _executor([Verdict(fail=["VER-CON-01"])])
    with pytest.raises(BlockedOrderError) as err:
        ex.manual([A], controller="SC-1")
    assert err.value.rules == ["VER-CON-01"] and sent == []


def test_auto_accept_is_only_for_evaluation_runs() -> None:
    with pytest.raises(ValueError):
        _executor([], auto_accept=True)
    ex, sent = _executor([], auto_accept=True, evaluation_mode=True)
    ex.present("R-1", "ctx", Option(option_id="A", actions=[A]))
    assert ex.lc.recs["R-1"].status is Status.ORDER_ISSUED and sent


# ------------------------------------------------------------------ in the twin, with the station masters


def test_an_accepted_order_reaches_the_station_masters_and_a_decline_is_replanned_in_budget() -> None:
    world = run.load_world()
    plans, _, _ = run.plans_for(world, DAY)
    train = next(
        p for p in plans if any(n.op == "CPP" and T0 + 15 * 60 < n.arr < T0 + 50 * 60 for n in p.nodes)
    )
    state: dict[str, Any] = {}

    def setup(sim: Sim) -> None:
        sm = StationMasters(sim, seed=3)
        ok = [
            sl.line_no
            for sl in world.layout.station_lines["CPP"]
            if sm.decide(
                Order(
                    order_id="p",
                    station="CPP",
                    run_id=train.run_id,
                    action="receive_on_line",
                    line_no=sl.line_no,
                )
            )
            is None
        ]
        loop_a, loop_b = ok[0], ok[1]
        Injector(
            sim,
            [
                Disruption(
                    incident_id="INC-PF",
                    kind="point_failure",
                    start_s=T0 - 600,
                    duration_s=7200,
                    station="CPP",
                    line_no=loop_a,
                )
            ],
        )

        def publish(topic: str, payload: dict[str, Any]) -> None:
            fields = {k: payload[k] for k in ("order_id", "station", "run_id", "line_no", "until_s")}
            sm.receive(Order(action=payload["action"], **fields))

        def replan(option: Option, declined: list[PlanAction]) -> Option | None:
            bad = {(a.station, a.line_no) for a in declined}
            for no in ok:
                if ("CPP", no) not in bad:
                    return Option(
                        option_id=f"{option.option_id}-R",
                        actions=[
                            PlanAction(kind="receive_on_line", run_id=train.run_id, station="CPP", line_no=no)
                        ],
                    )
            return None

        ex = Executor(Lifecycle(), publish, lambda o: Verdict(), replan, lambda: sim.env.now, validity_s=900)
        state.update(sm=sm, ex=ex, loop_b=loop_b)

        def controller() -> Any:
            yield sim.env.timeout(T0)
            option = Option(
                option_id="A",
                actions=[
                    PlanAction(kind="receive_on_line", run_id=train.run_id, station="CPP", line_no=loop_a)
                ],
            )
            ex.present("R-1", "CPP reception", option)
            ex.accept("R-1")
            while ex.lc.recs["R-1"].status is not Status.PRESENTED:  # declined and re-planned
                yield sim.env.timeout(30)
            ex.accept("R-1")

        sim.env.process(controller())

    def sink(topic: str, ev: EventEnvelope) -> None:
        if topic == "ctrl.order_acks" and "ex" in state:
            state["ex"].on_ack(ev.payload)

    run.run_day(world, DAY, until_s=T0 + 3600, sink=sink, setup=setup)
    ex: Executor = state["ex"]
    first, second = sorted(ex.orders.values(), key=lambda o: o.order_id)
    assert first.status == "DECLINED" and first.reason_code == "LOOP_OCCUPIED"
    assert ex.alerts and ex.alerts[0].priority == "P2"
    assert len(ex.replans) == 1 and ex.replans[0].wall_s < TIER1_BUDGET_S
    assert ex.replans[0].verdict is not None and ex.replans[0].verdict.releasable
    assert second.action.line_no == state["loop_b"] and second.status == "ACKNOWLEDGED"
    assert ex.lc.recs["R-1"].status is Status.EXECUTING
    assert all(state["sm"].lo <= a.latency_s <= state["sm"].hi for a in state["sm"].answers)
