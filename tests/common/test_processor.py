"""Consumer idempotency and dead-lettering without a broker (DQ-01, DQ-02; issue #19)."""

from __future__ import annotations

import json
import uuid

import pytest

from raildss_common.bus import EventProcessor, KafkaSettings, MemoryDedupStore, Rejection
from raildss_common.envelope import EventEnvelope, parse_event
from tests.common.events import SPEC_6_2_EXAMPLE, valid_fixtures


def test_duplicate_event_ids_have_no_duplicate_effects() -> None:
    seen: list[uuid.UUID] = []
    proc = EventProcessor(lambda e: seen.append(e.event_id))
    events = [json.dumps(ev).encode() for _, _, ev in valid_fixtures()]
    outcomes = [proc.process(raw) for raw in events + events + events[:3]]  # re-publish everything
    assert outcomes.count("delivered") == len(events)
    assert outcomes.count("duplicate") == len(events) + 3
    assert len(seen) == len(set(seen)) == len(events)
    assert (proc.stats.delivered, proc.stats.duplicates, proc.stats.rejected) == (
        len(events),
        len(events) + 3,
        0,
    )


def test_duplicate_with_different_body_is_still_dropped() -> None:
    """DQ-02 keys on event_id alone: a re-sent event with a new ingest_time is the same event."""
    handled: list[EventEnvelope] = []
    proc = EventProcessor(handled.append)
    first = json.dumps(SPEC_6_2_EXAMPLE).encode()
    again = json.dumps({**SPEC_6_2_EXAMPLE, "ingest_time": "2026-11-02T04:13:00Z"}).encode()
    assert [proc.process(first), proc.process(again)] == ["delivered", "duplicate"]
    assert len(handled) == 1


def test_rejected_records_reach_the_dead_letter_callback() -> None:
    rejected: list[Rejection] = []
    proc = EventProcessor(lambda e: None, on_reject=rejected.append)
    assert proc.process(b"{broken", topic="obs.position", partition=0, offset=7) == "rejected"
    assert rejected[0].topic == "obs.position" and rejected[0].offset == 7
    record = json.loads(rejected[0].dlq_record())
    assert record["reason"].startswith("not valid JSON") and record["raw"] == "{broken"


def test_failed_handler_does_not_mark_the_event_seen() -> None:
    calls: list[int] = []

    def flaky(_: EventEnvelope) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient")

    proc = EventProcessor(flaky)
    raw = json.dumps(SPEC_6_2_EXAMPLE).encode()
    with pytest.raises(RuntimeError):
        proc.process(raw)
    assert proc.process(raw) == "delivered"
    assert proc.process(raw) == "duplicate"
    assert len(calls) == 2


def test_memory_store_capacity_evicts_oldest() -> None:
    store = MemoryDedupStore(capacity=2)
    a, b, c = (uuid.uuid4() for _ in range(3))
    for x in (a, b, c):
        store.add(x)
    assert a not in store and b in store and c in store and len(store) == 2
    with pytest.raises(ValueError):
        MemoryDedupStore(capacity=0)


def test_settings_require_credentials_for_sasl() -> None:
    with pytest.raises(ValueError):
        KafkaSettings().client_config()
    conf = KafkaSettings(username="dss", password="pw").client_config()
    assert conf["sasl.username"] == "dss" and conf["security.protocol"] == "SASL_PLAINTEXT"
    assert "pw" not in repr(KafkaSettings(username="dss", password="pw"))


def test_parse_event_accepts_wire_bytes() -> None:
    ev = parse_event(json.dumps(SPEC_6_2_EXAMPLE).encode())
    assert EventProcessor(lambda e: None).process(ev.to_json()) == "delivered"
