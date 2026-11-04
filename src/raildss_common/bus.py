"""Kafka producer and consumer helpers (spec §6.2, REC-05, DQ-01, DQ-02, NFR-SEC-03; issue #19).

* :class:`EventProducer` validates each envelope before sending, keys it per the topic catalogue
  (``section_id`` by default) and uses Kafka's idempotent producer (no duplicates from retries).
* :class:`EventProcessor` is the transport-free core of consumption: decode and validate
  (invalid records go to a dead-letter callback with the reason, DQ-01), then drop any
  ``event_id`` already processed (DQ-02) before calling the handler exactly once per event.
* :class:`EventConsumer` feeds Kafka records to an :class:`EventProcessor` and commits each
  offset only after the record has been handled (at-least-once delivery, made effectively-once
  by the de-duplication store).

Clients authenticate with SASL (``RAILDSS_KAFKA_USERNAME`` / ``RAILDSS_KAFKA_PASSWORD``); the
broker's ACLs decide what each principal may read and write (see ``config/kafka/topics.yaml``).
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol
from uuid import UUID

from confluent_kafka import Consumer, KafkaError, KafkaException, Message, Producer

from raildss_common.envelope import EnvelopeError, EventEnvelope, parse_event
from raildss_common.topics import Catalogue, partition_key

ENV_PREFIX = "RAILDSS_KAFKA_"
DEFAULT_BOOTSTRAP = "localhost:9092"

Outcome = Literal["delivered", "duplicate", "rejected"]


# --------------------------------------------------------------------------- connection
@dataclass(frozen=True)
class KafkaSettings:
    """Connection settings for one principal."""

    bootstrap_servers: str = DEFAULT_BOOTSTRAP
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    security_protocol: str = "SASL_PLAINTEXT"
    sasl_mechanism: str = "PLAIN"

    @classmethod
    def from_env(cls, prefix: str = ENV_PREFIX) -> KafkaSettings:
        """Read ``<prefix>BOOTSTRAP``, ``USERNAME``, ``PASSWORD`` and ``SECURITY_PROTOCOL``."""
        return cls(
            bootstrap_servers=os.environ.get(f"{prefix}BOOTSTRAP", DEFAULT_BOOTSTRAP),
            username=os.environ.get(f"{prefix}USERNAME"),
            password=os.environ.get(f"{prefix}PASSWORD"),
            security_protocol=os.environ.get(f"{prefix}SECURITY_PROTOCOL", "SASL_PLAINTEXT"),
        )

    def as_principal(self, username: str, password: str) -> KafkaSettings:
        return KafkaSettings(self.bootstrap_servers, username, password, self.security_protocol)

    def client_config(self) -> dict[str, str | int | float | bool]:
        conf: dict[str, str | int | float | bool] = {
            "bootstrap.servers": self.bootstrap_servers,
            "security.protocol": self.security_protocol,
        }
        if self.security_protocol.startswith("SASL"):
            if not self.username or not self.password:
                raise ValueError("SASL needs a username and password (RAILDSS_KAFKA_USERNAME/PASSWORD)")
            conf |= {
                "sasl.mechanism": self.sasl_mechanism,
                "sasl.username": self.username,
                "sasl.password": self.password,
            }
        return conf


# --------------------------------------------------------------------------- de-duplication
class DedupStore(Protocol):
    """Remembers processed ``event_id`` values (DQ-02). Swap in a persistent store for restarts."""

    def __contains__(self, event_id: object) -> bool: ...

    def add(self, event_id: UUID) -> None: ...


class MemoryDedupStore:
    """In-memory store. ``capacity`` bounds memory (oldest ids are forgotten first); None = unbounded."""

    def __init__(self, capacity: int | None = None) -> None:
        if capacity is not None and capacity < 1:
            raise ValueError("capacity must be >= 1 or None")
        self.capacity = capacity
        self._ids: OrderedDict[UUID, None] = OrderedDict()

    def __contains__(self, event_id: object) -> bool:
        return event_id in self._ids

    def __len__(self) -> int:
        return len(self._ids)

    def add(self, event_id: UUID) -> None:
        self._ids[event_id] = None
        self._ids.move_to_end(event_id)
        if self.capacity is not None and len(self._ids) > self.capacity:
            self._ids.popitem(last=False)


# --------------------------------------------------------------------------- processing
@dataclass(frozen=True)
class Rejection:
    """A record that failed envelope validation, with where it came from (DQ-01)."""

    reason: str
    errors: tuple[str, ...]
    raw: bytes
    topic: str | None = None
    partition: int | None = None
    offset: int | None = None
    key: bytes | None = None

    def dlq_record(self) -> bytes:
        """JSON value written to the dead-letter topic."""
        return json.dumps(
            {
                "reason": self.reason,
                "errors": list(self.errors),
                "topic": self.topic,
                "partition": self.partition,
                "offset": self.offset,
                "raw": self.raw.decode("utf-8", errors="replace"),
            },
            ensure_ascii=False,
        ).encode("utf-8")


@dataclass
class ConsumerStats:
    delivered: int = 0
    duplicates: int = 0
    rejected: int = 0


class EventProcessor:
    """Validate, de-duplicate and dispatch events. Independent of the transport."""

    def __init__(
        self,
        handler: Callable[[EventEnvelope], None],
        *,
        store: DedupStore | None = None,
        on_reject: Callable[[Rejection], None] | None = None,
    ) -> None:
        self.handler = handler
        self.store: DedupStore = store if store is not None else MemoryDedupStore()
        self.on_reject = on_reject
        self.stats = ConsumerStats()

    def process(
        self,
        raw: bytes,
        *,
        topic: str | None = None,
        partition: int | None = None,
        offset: int | None = None,
        key: bytes | None = None,
    ) -> Outcome:
        try:
            event = parse_event(raw)
        except EnvelopeError as exc:
            self.stats.rejected += 1
            if self.on_reject is not None:
                self.on_reject(Rejection(exc.reason, tuple(exc.errors), raw, topic, partition, offset, key))
            return "rejected"
        if event.event_id in self.store:
            self.stats.duplicates += 1
            return "duplicate"
        self.handler(event)  # if this raises, the id is not recorded and the event can be retried
        self.store.add(event.event_id)
        self.stats.delivered += 1
        return "delivered"


# --------------------------------------------------------------------------- Kafka clients
class DeliveryError(RuntimeError):
    """One or more records were not acknowledged by the broker."""


class EventProducer:
    """Publish validated envelopes. Records are keyed per the catalogue (default ``section_id``)."""

    def __init__(
        self, settings: KafkaSettings, catalogue: Catalogue, *, extra: dict[str, Any] | None = None
    ) -> None:
        self.catalogue = catalogue
        conf: dict[str, Any] = settings.client_config() | {"enable.idempotence": True, "acks": "all"}
        self._producer = Producer(conf | (extra or {}))
        self._errors: list[str] = []

    def _on_delivery(self, err: KafkaError | None, msg: Message) -> None:
        if err is not None:
            self._errors.append(f"{msg.topic()}: {err.str()}")

    def publish(self, topic: str, event: EventEnvelope | dict[str, Any]) -> None:
        """Validate ``event`` (DQ-01) and queue it on ``topic``. Call :meth:`flush` to wait."""
        env = event if isinstance(event, EventEnvelope) else parse_event(event)
        t = self.catalogue.topic(topic)
        self.publish_raw(
            t.name,
            env.to_json(),
            key=partition_key(t, env),
            headers=[("event_type", env.event_type.value), ("schema_version", env.schema_version)],
        )

    def publish_raw(
        self,
        topic: str,
        value: bytes,
        *,
        key: bytes | None = None,
        headers: list[tuple[str, str | bytes | None]] | None = None,
    ) -> None:
        """Queue bytes without validation (dead letters, and tests that need malformed records)."""
        self.catalogue.topic(topic)
        self._producer.produce(topic, value=value, key=key, headers=headers, on_delivery=self._on_delivery)
        self._producer.poll(0)

    def flush(self, timeout_s: float | None = None) -> None:
        remaining = self._producer.flush(self.catalogue.timeout_s if timeout_s is None else timeout_s)
        errors, self._errors = self._errors, []
        if remaining or errors:
            raise DeliveryError(f"{remaining} record(s) not delivered; errors: {errors}")


class EventConsumer:
    """Consume envelopes into an :class:`EventProcessor`; rejected records go to the topic's DLQ."""

    def __init__(
        self,
        settings: KafkaSettings,
        catalogue: Catalogue,
        *,
        group_id: str,
        topics: Iterable[str],
        processor: EventProcessor,
        dlq_producer: EventProducer | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.catalogue = catalogue
        self.processor = processor
        self.dlq_producer = dlq_producer
        self.topics = [catalogue.topic(t).name for t in topics]
        conf: dict[str, Any] = settings.client_config() | {
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
        self._consumer = Consumer(conf | (extra or {}))
        self._consumer.subscribe(self.topics)
        if dlq_producer is not None and processor.on_reject is None:
            processor.on_reject = self._dead_letter

    def _dead_letter(self, rejection: Rejection) -> None:
        assert self.dlq_producer is not None
        dlq = self.catalogue.topic(rejection.topic).dlq if rejection.topic else None
        if dlq is not None:
            self.dlq_producer.publish_raw(dlq, rejection.dlq_record(), key=rejection.key)
            self.dlq_producer.flush()

    def poll(self, timeout_s: float) -> Outcome | None:
        """Handle at most one record. Broker errors (e.g. authorisation failures) raise KafkaException."""
        msg = self._consumer.poll(timeout_s)
        if msg is None:
            return None
        err = msg.error()
        if err is not None:
            raise KafkaException(err)
        outcome = self.processor.process(
            msg.value() or b"",
            topic=msg.topic(),
            partition=msg.partition(),
            offset=msg.offset(),
            key=msg.key(),
        )
        self._consumer.commit(message=msg, asynchronous=False)
        return outcome

    def close(self) -> None:
        self._consumer.close()
