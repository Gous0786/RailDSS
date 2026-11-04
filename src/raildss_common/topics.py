"""Kafka topic catalogue and access-control model (spec §7.1-7.2, FR-TWIN-14, DQ-12; issue #19).

The catalogue lives in ``config/kafka/topics.yaml``. Loading it checks that:

* every topic belongs to a known family (``obs``, ``ops``, ``gt``, ``dss``, ``ctrl``, ``audit``, ``dlq``),
* every grant names a catalogued topic or a prefix that matches at least one,
* no ``dss`` principal is granted anything on a ``gt.*`` topic (ground-truth isolation, DQ-12).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from raildss_common.envelope import EventEnvelope

REPO = Path(__file__).resolve().parents[2]
DEFAULT_CATALOGUE = REPO / "config" / "kafka" / "topics.yaml"

FAMILIES = ("obs", "ops", "gt", "dss", "ctrl", "audit", "dlq")
GROUND_TRUTH_PREFIX = "gt."
ROLES = ("dss", "simulator", "evaluation")
DSS_ROLE = "dss"
PAYLOAD_KEY_PREFIX = "payload."
SECTION_KEY = "section_id"


class CatalogueError(ValueError):
    """The topic catalogue is inconsistent or breaks ground-truth isolation."""


@dataclass(frozen=True)
class Topic:
    name: str
    partitions: int
    replication_factor: int
    key: str
    config: Mapping[str, str]
    description: str = ""

    @property
    def family(self) -> str:
        return self.name.split(".", 1)[0]

    @property
    def is_ground_truth(self) -> bool:
        return self.name.startswith(GROUND_TRUTH_PREFIX)

    @property
    def dlq(self) -> str | None:
        """Dead-letter topic for records rejected on this topic (None for gt.* and dlq.*)."""
        if self.family in ("gt", "dlq"):
            return None
        return f"dlq.{self.family}" if self.family in ("obs", "ops") else "dlq.internal"


@dataclass(frozen=True)
class Grant:
    """One topic grant. ``prefixed`` grants cover every topic whose name starts with ``pattern``."""

    pattern: str
    prefixed: bool

    @classmethod
    def parse(cls, entry: str) -> Grant:
        return cls(entry, entry.endswith("."))

    def matches(self, topic: str) -> bool:
        return topic.startswith(self.pattern) if self.prefixed else topic == self.pattern


@dataclass(frozen=True)
class Principal:
    name: str
    role: str
    read: tuple[Grant, ...] = ()
    write: tuple[Grant, ...] = ()
    description: str = ""

    @property
    def kafka_principal(self) -> str:
        return f"User:{self.name}"

    @property
    def group_prefix(self) -> str:
        """Consumer group ids this principal may use start with this prefix."""
        return f"{self.name}."

    @property
    def is_dss(self) -> bool:
        return self.role == DSS_ROLE


@dataclass(frozen=True)
class Catalogue:
    topics: tuple[Topic, ...]
    principals: tuple[Principal, ...]
    timeout_s: float
    path: Path | None = field(default=None, compare=False)

    def topic(self, name: str) -> Topic:
        for t in self.topics:
            if t.name == name:
                return t
        raise KeyError(f"topic {name!r} is not in the catalogue")

    def principal(self, name: str) -> Principal:
        for p in self.principals:
            if p.name == name:
                return p
        raise KeyError(f"principal {name!r} is not in the catalogue")

    def matching(self, grant: Grant) -> list[Topic]:
        return [t for t in self.topics if grant.matches(t.name)]

    def can_read(self, principal: str, topic: str) -> bool:
        return any(g.matches(topic) for g in self.principal(principal).read)


def partition_key(topic: Topic, event: EventEnvelope) -> bytes:
    """Record key for ``event`` on ``topic`` (spec §7.2 key column; default ``section_id``)."""
    if topic.key == SECTION_KEY:
        return event.section_id.encode("utf-8")
    field_name = topic.key.removeprefix(PAYLOAD_KEY_PREFIX)
    value = event.payload.get(field_name)
    if value is None or isinstance(value, dict | list):
        raise KeyError(f"{topic.name} is keyed by {topic.key}, which the {event.event_type} payload lacks")
    return str(value).encode("utf-8")


def _grants(entries: Any, where: str) -> tuple[Grant, ...]:
    if entries is None:
        return ()
    if not isinstance(entries, list) or not all(isinstance(e, str) and e for e in entries):
        raise CatalogueError(f"{where}: expected a list of topic names or prefixes")
    return tuple(Grant.parse(e) for e in entries)


def parse_catalogue(doc: Mapping[str, Any], path: Path | None = None) -> Catalogue:
    """Build and validate a :class:`Catalogue` from the parsed YAML document."""
    defaults = doc.get("defaults") or {}
    base_config = {str(k): str(v) for k, v in (defaults.get("config") or {}).items()}
    topics: list[Topic] = []
    for raw in doc.get("topics") or []:
        name = str(raw["name"])
        config = {**base_config, **{str(k): str(v) for k, v in (raw.get("config") or {}).items()}}
        topic = Topic(
            name=name,
            partitions=int(raw.get("partitions", defaults.get("partitions", 1))),
            replication_factor=int(raw.get("replication_factor", defaults.get("replication_factor", 1))),
            key=str(raw.get("key", defaults.get("key", SECTION_KEY))),
            config=config,
            description=str(raw.get("description", "")),
        )
        if topic.family not in FAMILIES:
            raise CatalogueError(f"topic {name}: unknown family {topic.family!r} (expected {FAMILIES})")
        if topic.key != SECTION_KEY and not topic.key.startswith(PAYLOAD_KEY_PREFIX):
            raise CatalogueError(f"topic {name}: key must be section_id or payload.<field>")
        if topic.partitions < 1 or topic.replication_factor < 1:
            raise CatalogueError(f"topic {name}: partitions and replication_factor must be >= 1")
        topics.append(topic)
    names = [t.name for t in topics]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise CatalogueError(f"duplicate topics: {', '.join(dupes)}")

    principals: list[Principal] = []
    for raw in doc.get("principals") or []:
        name = str(raw["name"])
        role = str(raw.get("role", ""))
        if role not in ROLES:
            raise CatalogueError(f"principal {name}: unknown role {role!r} (expected {ROLES})")
        principals.append(
            Principal(
                name=name,
                role=role,
                read=_grants(raw.get("read"), f"principal {name} read"),
                write=_grants(raw.get("write"), f"principal {name} write"),
                description=str(raw.get("description", "")),
            )
        )

    catalogue = Catalogue(
        topics=tuple(topics),
        principals=tuple(principals),
        timeout_s=float((doc.get("client") or {}).get("timeout_s", 1)),
        path=path,
    )
    problems = lint(catalogue)
    if problems:
        raise CatalogueError("; ".join(problems))
    return catalogue


def lint(catalogue: Catalogue) -> list[str]:
    """Static checks, including ground-truth isolation for DSS principals (DQ-12)."""
    problems: list[str] = []
    for p in catalogue.principals:
        for op, grants in (("read", p.read), ("write", p.write)):
            for g in grants:
                matched = catalogue.matching(g)
                if not matched:
                    problems.append(f"principal {p.name}: {op} grant {g.pattern!r} matches no topic")
                gt = [t.name for t in matched if t.is_ground_truth]
                if p.is_dss and gt:
                    problems.append(
                        f"principal {p.name} (role dss): {op} grant {g.pattern!r} reaches ground truth "
                        f"{gt} (FR-TWIN-14, DQ-12)"
                    )
    return problems


def load_catalogue(path: Path | str | None = None) -> Catalogue:
    """Load ``config/kafka/topics.yaml`` (or ``path``)."""
    p = Path(path) if path is not None else DEFAULT_CATALOGUE
    with p.open(encoding="utf-8") as fh:
        doc = yaml.safe_load(fh)
    if not isinstance(doc, Mapping):
        raise CatalogueError(f"{p}: expected a mapping at the top level")
    return parse_catalogue(doc, p)
