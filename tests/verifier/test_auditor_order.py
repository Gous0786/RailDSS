"""The auditor orders a merged ground-truth stream by time, not by the per-source ``seq`` (DQ-03)."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from raildss_common.envelope import EventEnvelope, EventType
from raildss_verifier.auditor import TrainMeta, plan_from_trace
from tests.verifier.test_mutation import NET

ORIGIN = dt.datetime(2025, 8, 20, tzinfo=dt.UTC)


def ev(source: str, seq: int, t: float, etype: EventType, payload: dict[str, Any]) -> EventEnvelope:
    return EventEnvelope(
        event_id=uuid.uuid5(uuid.NAMESPACE_URL, f"{source}/{seq}"),
        event_type=etype,
        schema_version="1.0",
        source=source,
        section_id="KTV-PSA",
        network_version="TOY",
        scenario_id="order-test",
        seq=seq,
        event_time=ORIGIN + dt.timedelta(seconds=t),
        train_run_id="T1",
        payload=payload,
    )


def occ(seq: int, t: float, label: str, occupied: bool = True) -> EventEnvelope:
    return ev("SIM.TDS", seq, t, EventType.TDS_OCCUPANCY, {"tds_id": label, "occupied": occupied})


def test_two_sources_with_overlapping_seq_are_applied_in_time_order() -> None:
    tds = [
        occ(1, 0, "SL:A/1"),
        ev("SIM.TDS", 2, 60, EventType.ROUTE_SET, {"mode": "send", "block": "A-B", "block_line": "1"}),
        occ(3, 360, "SL:B/1"),
        ev("SIM.TDS", 4, 480, EventType.ROUTE_SET, {"mode": "send", "block": "B-C", "block_line": "DN"}),
        occ(5, 780, "SL:C/1"),
        occ(6, 840, "SL:C/1", occupied=False),
    ]
    # the station feed counts from 1 too: by seq alone its record would land before any call exists
    sm = [ev("SIM.SM", 1, 470, EventType.DELAY_RECORD, {"station": "B", "kind": "departure", "delay_s": 0})]
    meta = {"T1": TrainMeta(kind="passenger", length_m=500, booked_dep={"B": 480})}
    plan = plan_from_trace(sm + tds[::-1], meta, NET, ORIGIN)
    (t1,) = plan.trains
    assert [(c.op, c.arr_s, c.dep_s) for c in t1.calls] == [("A", 0, 60), ("B", 360, 480), ("C", 780, 840)]
    assert [(r.block_id, r.enter_s, r.clear_s) for r in t1.runs] == [("A-B", 60, 360), ("B-C", 480, 780)]
    assert t1.calls[1].stop and t1.calls[1].booked_dep_s == 480
