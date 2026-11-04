"""Kafka integration tests: topics, ACLs, ground-truth isolation, duplicates, DLQ (issue #19).

Need the docker-compose broker (SASL/PLAIN + StandardAuthorizer)::

    docker compose up -d --wait kafka
    RAILDSS_TEST_KAFKA_BOOTSTRAP=localhost:9092 pytest -m kafka

Passwords are read from KAFKA_<PRINCIPAL>_PASSWORD (as in .env). Skipped otherwise; the CI job
``kafka`` runs them.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from confluent_kafka import Consumer, KafkaError

from raildss_common.admin import admin_client, check_isolation, ensure_acls, ensure_topics
from raildss_common.bus import EventConsumer, EventProcessor, EventProducer, KafkaSettings
from raildss_common.envelope import EventEnvelope
from raildss_common.topics import Catalogue, load_catalogue
from tests.common.events import envelope

BOOTSTRAP = os.environ.get("RAILDSS_TEST_KAFKA_BOOTSTRAP")
pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(not BOOTSTRAP, reason="RAILDSS_TEST_KAFKA_BOOTSTRAP not set (Kafka tests run in CI)"),
]
POLL_S = 1.0
WAIT_S = 30.0


def settings(principal: str) -> KafkaSettings:
    assert BOOTSTRAP
    password = os.environ[f"KAFKA_{principal.upper()}_PASSWORD"]
    return KafkaSettings(BOOTSTRAP, principal, password)


@pytest.fixture(scope="module")
def catalogue() -> Iterator[Catalogue]:
    cat = load_catalogue()
    admin = admin_client(settings("admin"))
    ensure_topics(admin, cat)
    ensure_acls(admin, cat)
    time.sleep(2)  # let the ACL metadata propagate to the broker's authorizer
    yield cat


def _event(event_type: str = "TRAIN_POSITION", **overrides: Any) -> EventEnvelope:
    run = f"{uuid.uuid4().hex[:6]}@2026-11-02"
    ev = envelope(event_type, "SIM.RTIS", {"speed_kmh": 50.0}, train_run_id=run, location={"km": 10.0})
    ev["event_id"] = str(uuid.uuid4())
    ev.update(overrides)
    return EventEnvelope.model_validate(ev)


def _drain(consumer: EventConsumer, expected: int) -> list[str]:
    outcomes: list[str] = []
    deadline = time.monotonic() + WAIT_S
    while len(outcomes) < expected and time.monotonic() < deadline:
        out = consumer.poll(POLL_S)
        if out is not None:
            outcomes.append(out)
    return outcomes


def test_topic_and_acl_creation_is_idempotent(catalogue: Catalogue) -> None:
    admin = admin_client(settings("admin"))
    topics = ensure_topics(admin, catalogue)
    acls = ensure_acls(admin, catalogue)
    assert not topics.changed and not topics.drift and not acls.changed
    assert sorted(topics.unchanged) == sorted(t.name for t in catalogue.topics)


def test_ground_truth_isolation_deployment_check(catalogue: Catalogue) -> None:
    assert check_isolation(admin_client(settings("admin")), catalogue) == []


def test_dss_principal_is_denied_reading_gt_state(catalogue: Catalogue) -> None:
    """FR-TWIN-14 / DQ-12: the twin publishes ground truth; a DSS principal cannot read it."""
    twin = EventProducer(settings("twin"), catalogue)
    twin.publish("gt.state", _event("TRAIN_STATE", source="SIM.GT"))
    twin.flush()

    group = f"dss.acl-test-{uuid.uuid4().hex[:8]}"
    consumer = Consumer(
        settings("dss").client_config() | {"group.id": group, "auto.offset.reset": "earliest"}
    )
    consumer.subscribe(["gt.state"])
    error: KafkaError | None = None
    received = 0
    deadline = time.monotonic() + WAIT_S
    try:
        while error is None and time.monotonic() < deadline:
            msg = consumer.poll(POLL_S)
            if msg is None:
                continue
            if msg.error() is not None:
                error = msg.error()
            else:
                received += 1
    finally:
        consumer.close()
    assert received == 0
    assert error is not None, "expected an authorisation error for dss on gt.state"
    assert error.code() == KafkaError.TOPIC_AUTHORIZATION_FAILED, error.str()

    # The metadata path is denied too (no topic description for gt.* either).
    meta = Consumer(settings("dss").client_config() | {"group.id": group})
    try:
        topic_meta = meta.list_topics("gt.state", timeout=WAIT_S).topics["gt.state"]
        assert topic_meta.error is not None
        assert topic_meta.error.code() == KafkaError.TOPIC_AUTHORIZATION_FAILED
    finally:
        meta.close()


def test_dss_principal_cannot_write_ground_truth(catalogue: Catalogue) -> None:
    producer = EventProducer(settings("dss"), catalogue)
    producer.publish("gt.state", _event("TRAIN_STATE", source="SIM.GT"))
    with pytest.raises(Exception, match=r"(?i)authori[sz]"):
        producer.flush()


def test_evaluation_reads_gt_and_dss_reads_obs(catalogue: Catalogue) -> None:
    twin = EventProducer(settings("twin"), catalogue)
    gt_event, obs_event = _event("TRAIN_STATE", source="SIM.GT"), _event()
    twin.publish("gt.state", gt_event)
    twin.publish("obs.position", obs_event)
    twin.flush()
    for principal, topic, wanted in (
        ("evaluation", "gt.state", gt_event),
        ("dss", "obs.position", obs_event),
    ):
        got: list[EventEnvelope] = []
        consumer = EventConsumer(
            settings(principal),
            catalogue,
            group_id=f"{principal}.read-test-{uuid.uuid4().hex[:8]}",
            topics=[topic],
            processor=EventProcessor(got.append),
        )
        try:
            deadline = time.monotonic() + WAIT_S
            while wanted not in got and time.monotonic() < deadline:
                consumer.poll(POLL_S)
        finally:
            consumer.close()
        assert wanted in got, f"{principal} did not receive its event from {topic}"


def test_republished_duplicates_have_no_duplicate_effects(catalogue: Catalogue) -> None:
    """DQ-02 end to end: every event published three times is handled once."""
    twin = EventProducer(settings("twin"), catalogue)
    section = f"T{uuid.uuid4().hex[:6].upper()}"  # isolates this run's records
    events = [_event(section_id=section, seq=i) for i in range(5)]
    for _ in range(3):
        for ev in events:
            twin.publish("obs.position", ev)
    twin.flush()

    handled: list[EventEnvelope] = []

    def handler(ev: EventEnvelope) -> None:
        if ev.section_id == section:
            handled.append(ev)

    consumer = EventConsumer(
        settings("dss"),
        catalogue,
        group_id=f"dss.dup-test-{uuid.uuid4().hex[:8]}",
        topics=["obs.position"],
        processor=EventProcessor(handler),
    )
    try:
        deadline = time.monotonic() + WAIT_S
        while len(handled) < len(events) and time.monotonic() < deadline:
            consumer.poll(POLL_S)
        # keep reading the remaining copies so the duplicates are actually seen
        for _ in range(3 * len(events)):
            consumer.poll(POLL_S)
    finally:
        consumer.close()
    assert sorted(e.event_id for e in handled) == sorted(e.event_id for e in events)
    assert consumer.processor.stats.duplicates >= 2 * len(events)


def test_malformed_record_is_dead_lettered(catalogue: Catalogue) -> None:
    twin = EventProducer(settings("twin"), catalogue)
    marker = uuid.uuid4().hex
    twin.publish_raw("obs.position", f'{{"marker": "{marker}"}}'.encode(), key=b"KTV-PSA")
    twin.flush()
    dss_producer = EventProducer(settings("dss"), catalogue)
    consumer = EventConsumer(
        settings("dss"),
        catalogue,
        group_id=f"dss.dlq-test-{uuid.uuid4().hex[:8]}",
        topics=["obs.position"],
        processor=EventProcessor(lambda e: None),
        dlq_producer=dss_producer,
    )
    try:
        deadline = time.monotonic() + WAIT_S
        while consumer.processor.stats.rejected == 0 and time.monotonic() < deadline:
            consumer.poll(POLL_S)
    finally:
        consumer.close()
    assert consumer.processor.stats.rejected >= 1

    admin_reader = Consumer(
        settings("admin").client_config()
        | {"group.id": f"admin.dlq-{uuid.uuid4().hex[:8]}", "auto.offset.reset": "earliest"}
    )
    admin_reader.subscribe(["dlq.obs"])
    found = False
    deadline = time.monotonic() + WAIT_S
    try:
        while not found and time.monotonic() < deadline:
            msg = admin_reader.poll(POLL_S)
            if msg is not None and msg.error() is None and marker.encode() in (msg.value() or b""):
                found = True
    finally:
        admin_reader.close()
    assert found, "malformed record did not reach dlq.obs"
