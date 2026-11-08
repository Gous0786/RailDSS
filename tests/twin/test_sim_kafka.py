"""A x60 twin run streams gt.* events to Kafka (issue #20). Needs the docker-compose broker.

    docker compose up -d --wait kafka
    RAILDSS_TEST_KAFKA_BOOTSTRAP=localhost:9092 pytest -m kafka tests/twin

Passwords are read from KAFKA_<PRINCIPAL>_PASSWORD (as in .env). Skipped otherwise; the CI job
``kafka`` runs them. The twin publishes as the ``twin`` principal and the ``evaluation`` principal
reads, as FR-TWIN-14 requires.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
import uuid
from collections.abc import Iterator

import pytest
from confluent_kafka import Consumer

from raildss_common.admin import admin_client, ensure_acls, ensure_topics
from raildss_common.bus import EventProducer, KafkaSettings
from raildss_common.topics import Catalogue, load_catalogue
from raildss_twin import run
from raildss_twin.clock import Clock

BOOTSTRAP = os.environ.get("RAILDSS_TEST_KAFKA_BOOTSTRAP")
pytestmark = [
    pytest.mark.kafka,
    pytest.mark.skipif(not BOOTSTRAP, reason="RAILDSS_TEST_KAFKA_BOOTSTRAP not set (Kafka tests run in CI)"),
]
GT_TOPICS = ["gt.state", "gt.occupancy", "gt.signal", "gt.delay"]
SIM_SECONDS = 600  # ten simulated minutes: about ten seconds of wall time at x60
POLL_S = 1.0
WAIT_S = 60.0


def settings(principal: str) -> KafkaSettings:
    assert BOOTSTRAP
    return KafkaSettings(BOOTSTRAP, principal, os.environ[f"KAFKA_{principal.upper()}_PASSWORD"])


@pytest.fixture(scope="module")
def catalogue() -> Iterator[Catalogue]:
    cat = load_catalogue()
    admin = admin_client(settings("admin"))
    ensure_topics(admin, cat)
    ensure_acls(admin, cat)
    time.sleep(2)  # let the ACL metadata reach the broker's authorizer
    yield cat


def test_x60_run_streams_gt_events_to_kafka(catalogue: Catalogue) -> None:
    world = run.load_world()
    scenario = f"kafka-test-{uuid.uuid4().hex[:8]}"
    producer = EventProducer(settings("twin"), catalogue)
    clock = Clock(60.0, float(world.register.value("twin.sim")["max_speed_factor"]))
    start = time.monotonic()
    report, _ = run.run_day(
        world,
        dt.date(2025, 8, 20),
        sink=producer.publish,
        clock=clock,
        until_s=SIM_SECONDS,
        scenario_id=scenario,
    )
    producer.flush()
    wall = time.monotonic() - start
    assert SIM_SECONDS / 60 * 0.8 < wall < SIM_SECONDS / 60 * 3 + 30  # followed the wall clock at x60
    expected = sum(report.events.values())
    assert expected > 0

    consumer = Consumer(
        settings("evaluation").client_config()
        | {"group.id": f"evaluation.twin-{uuid.uuid4().hex[:8]}", "auto.offset.reset": "earliest"}
    )
    consumer.subscribe(GT_TOPICS)
    seen: dict[str, int] = {}
    deadline = time.monotonic() + WAIT_S
    try:
        while sum(seen.values()) < expected and time.monotonic() < deadline:
            msg = consumer.poll(POLL_S)
            if msg is None or msg.error() is not None:
                continue
            event = json.loads(msg.value())
            if event["scenario_id"] == scenario:
                seen[msg.topic()] = seen.get(msg.topic(), 0) + 1
    finally:
        consumer.close()
    assert seen == report.events
