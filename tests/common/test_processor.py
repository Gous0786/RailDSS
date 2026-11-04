"""Consumer idempotency and dead-lettering without a broker (DQ-01, DQ-02; issue #19)."""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid

import pytest

from raildss_common.bus import EventConsumer, EventProcessor, KafkaSettings, MemoryDedupStore, Rejection
from raildss_common.envelope import EnvelopeError, EventEnvelope, parse_event
from raildss_common.topics import load_catalogue
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


# --------------------------------------------------------------------------- review follow-ups
class _Msg:
    def __init__(self, value: bytes, topic: str = "obs.position", offset: int = 5) -> None:
        self._value, self._topic, self._offset = value, topic, offset

    def error(self) -> None:
        return None

    def value(self) -> bytes:
        return self._value

    def key(self) -> bytes:
        return b"KTV-PSA"

    def topic(self) -> str:
        return self._topic

    def partition(self) -> int:
        return 0

    def offset(self) -> int:
        return self._offset


class _FakeClient:
    """Stands in for confluent_kafka.Consumer: replays queued records, honours seek()."""

    def __init__(self, msgs: list[_Msg]) -> None:
        self.msgs = msgs
        self.pos = 0
        self.seeks: list[tuple[str, int, int]] = []
        self.commits: list[int] = []

    def subscribe(self, topics: list[str]) -> None:
        self.topics = topics

    def poll(self, timeout: float) -> _Msg | None:
        if self.pos >= len(self.msgs):
            return None
        msg = self.msgs[self.pos]
        self.pos += 1
        return msg

    def seek(self, tp: object) -> None:
        self.seeks.append((tp.topic, tp.partition, tp.offset))  # type: ignore[attr-defined]
        self.pos = next(i for i, m in enumerate(self.msgs) if m.offset() == tp.offset)  # type: ignore[attr-defined]

    def commit(self, message: _Msg, asynchronous: bool) -> None:
        self.commits.append(message.offset())


class _FakeDlqProducer:
    def __init__(self) -> None:
        self.sent: list[tuple[str, bytes]] = []

    def publish_raw(self, topic: str, value: bytes, *, key: bytes | None = None) -> None:
        self.sent.append((topic, value))

    def flush(self) -> None:
        pass


def _consumer(
    client: _FakeClient, processor: EventProcessor, topic: str, dlq: object = None
) -> EventConsumer:
    return EventConsumer(
        KafkaSettings(security_protocol="PLAINTEXT"),
        load_catalogue(),
        group_id="dss.test",
        topics=[topic],
        processor=processor,
        dlq_producer=dlq,  # type: ignore[arg-type]
        client=client,  # type: ignore[arg-type]
    )


def test_handler_failure_seeks_back_and_retries_the_same_record() -> None:
    good = json.dumps(SPEC_6_2_EXAMPLE).encode()
    second = json.dumps({**SPEC_6_2_EXAMPLE, "event_id": str(uuid.uuid4())}).encode()
    client = _FakeClient([_Msg(good, offset=5), _Msg(second, offset=6)])
    order: list[int] = []
    fail = [True]

    def handler(ev: EventEnvelope) -> None:
        if fail.pop() if fail else False:
            raise RuntimeError("transient")
        order.append(ev.seq)

    consumer = _consumer(client, EventProcessor(handler), "obs.position")
    with pytest.raises(RuntimeError):
        consumer.poll(0)
    assert client.seeks == [("obs.position", 0, 5)] and client.commits == []
    assert consumer.poll(0) == "delivered"  # the failed record again, not offset 6
    assert client.commits == [5]
    assert consumer.poll(0) == "delivered"
    assert client.commits == [5, 6]


def test_rejection_without_dlq_topic_is_counted_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    dlq = _FakeDlqProducer()
    client = _FakeClient([_Msg(b"{broken", topic="gt.state")])
    consumer = _consumer(client, EventProcessor(lambda e: None), "gt.state", dlq)
    with caplog.at_level(logging.ERROR, logger="raildss_common.bus"):
        assert consumer.poll(0) == "rejected"
    assert dlq.sent == []
    assert consumer.processor.stats.rejected == consumer.processor.stats.unrouted == 1
    assert "not dead-lettered" in caplog.text and "gt.state" in caplog.text


def test_rejection_with_dlq_topic_is_dead_lettered() -> None:
    dlq = _FakeDlqProducer()
    consumer = _consumer(_FakeClient([_Msg(b"{broken")]), EventProcessor(lambda e: None), "obs.position", dlq)
    assert consumer.poll(0) == "rejected"
    assert [t for t, _ in dlq.sent] == ["dlq.obs"]
    assert consumer.processor.stats.unrouted == 0


def test_processor_without_reject_handler_counts_unrouted(caplog: pytest.LogCaptureFixture) -> None:
    proc = EventProcessor(lambda e: None)
    with caplog.at_level(logging.ERROR, logger="raildss_common.bus"):
        assert proc.process(b"[]", topic="obs.position") == "rejected"
    assert proc.stats.unrouted == 1 and "no dead-letter handler" in caplog.text


def test_as_principal_keeps_every_other_field() -> None:
    base = KafkaSettings(
        "broker:9093", "admin", "a", security_protocol="SASL_SSL", sasl_mechanism="SCRAM-SHA-512"
    )
    dss = base.as_principal("dss", "d")
    assert dss == dataclasses.replace(base, username="dss", password="d")
    assert dss.sasl_mechanism == "SCRAM-SHA-512" and dss.security_protocol == "SASL_SSL"


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_are_rejected(constant: str) -> None:
    raw = json.dumps(SPEC_6_2_EXAMPLE).replace('"speed_kmh": 74.0', f'"speed_kmh": {constant}')
    assert constant in raw
    with pytest.raises(EnvelopeError):
        parse_event(raw.encode())
    with pytest.raises(EnvelopeError):  # the same value arriving as a Python float
        parse_event(
            {**SPEC_6_2_EXAMPLE, "payload": {"speed_kmh": float(constant.lower().replace("infinity", "inf"))}}
        )
    with pytest.raises(EnvelopeError):  # a non-finite number in a typed location field
        parse_event({**SPEC_6_2_EXAMPLE, "location": {"km": float("nan")}})
