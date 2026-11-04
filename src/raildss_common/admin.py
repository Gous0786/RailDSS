"""Apply the topic catalogue and ACLs to a broker; check ground-truth isolation (issue #19).

* :func:`ensure_topics` is idempotent: it creates missing topics, aligns the catalogued topic
  configs, and reports partition or replication drift (never shrinks or deletes anything).
* :func:`ensure_acls` adds the ACLs the catalogue implies and reports any others found on
  managed principals.
* :func:`check_isolation` is the DQ-12 deployment check: authorisation is enforced, unknown
  principals are denied by default, and no ACL lets a DSS principal (or ``User:*``) touch ``gt.*``.

The admin client must authenticate as a super user (``admin`` in docker-compose.yml).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import (
    AclBinding,
    AclBindingFilter,
    AclOperation,
    AclPermissionType,
    AdminClient,
    AlterConfigOpType,
    ConfigEntry,
    ConfigResource,
    NewTopic,
    ResourcePatternType,
    ResourceType,
)

from raildss_common.bus import KafkaSettings
from raildss_common.topics import GROUND_TRUTH_PREFIX, Catalogue, Grant, Principal

WILDCARD_PRINCIPAL = "User:*"
WILDCARD_RESOURCE = "*"
ANY_HOST = "*"


def admin_client(settings: KafkaSettings) -> AdminClient:
    return AdminClient(settings.client_config())


# --------------------------------------------------------------------------- topics
@dataclass
class TopicReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    drift: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.created or self.updated)


def ensure_topics(admin: AdminClient, catalogue: Catalogue) -> TopicReport:
    """Create missing topics and align configs. Safe to run any number of times."""
    timeout = catalogue.timeout_s
    report = TopicReport()
    existing = admin.list_topics(timeout=timeout).topics
    missing = [t for t in catalogue.topics if t.name not in existing]
    if missing:
        futures = admin.create_topics(
            [NewTopic(t.name, t.partitions, t.replication_factor, config=dict(t.config)) for t in missing],
            request_timeout=timeout,
        )
        for name, fut in futures.items():
            try:
                fut.result()
                report.created.append(name)
            except KafkaException as exc:  # a concurrent run created it first
                if exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                    raise
    created = set(report.created)
    present = [t for t in catalogue.topics if t.name not in created]
    if not present:
        return report
    metadata = admin.list_topics(timeout=timeout).topics
    resources = [ConfigResource(ResourceType.TOPIC, t.name) for t in present]
    described = admin.describe_configs(resources, request_timeout=timeout)
    current = {res.name: fut.result() for res, fut in described.items()}
    for t in present:
        meta = metadata[t.name]
        if len(meta.partitions) != t.partitions:
            report.drift.append(f"{t.name}: {len(meta.partitions)} partitions, catalogue says {t.partitions}")
        replicas = {len(p.replicas) for p in meta.partitions.values()}
        if replicas != {t.replication_factor}:
            report.drift.append(
                f"{t.name}: replication {sorted(replicas)}, catalogue says {t.replication_factor}"
            )
        diff = {
            k: v for k, v in t.config.items() if k not in current[t.name] or current[t.name][k].value != v
        }
        if not diff:
            report.unchanged.append(t.name)
            continue
        res = ConfigResource(
            ResourceType.TOPIC,
            t.name,
            incremental_configs=[
                ConfigEntry(k, v, incremental_operation=AlterConfigOpType.SET) for k, v in diff.items()
            ],
        )
        for fut in admin.incremental_alter_configs([res], request_timeout=timeout).values():
            fut.result()
        report.updated.append(t.name)
    return report


# --------------------------------------------------------------------------- ACLs
def _pattern_type(grant: Grant) -> ResourcePatternType:
    return ResourcePatternType.PREFIXED if grant.prefixed else ResourcePatternType.LITERAL


def acl_bindings(catalogue: Catalogue) -> list[AclBinding]:
    """Every ALLOW binding the catalogue implies. Kafka denies anything not listed."""
    out: list[AclBinding] = []
    for p in catalogue.principals:
        allow = (p.kafka_principal, ANY_HOST)
        for g in p.read:
            for op in (AclOperation.READ, AclOperation.DESCRIBE):
                out.append(
                    AclBinding(
                        ResourceType.TOPIC, g.pattern, _pattern_type(g), *allow, op, AclPermissionType.ALLOW
                    )
                )
        for g in p.write:
            for op in (AclOperation.WRITE, AclOperation.DESCRIBE):
                out.append(
                    AclBinding(
                        ResourceType.TOPIC, g.pattern, _pattern_type(g), *allow, op, AclPermissionType.ALLOW
                    )
                )
        if p.read:
            out.append(
                AclBinding(
                    ResourceType.GROUP,
                    p.group_prefix,
                    ResourcePatternType.PREFIXED,
                    *allow,
                    AclOperation.READ,
                    AclPermissionType.ALLOW,
                )
            )
    return out


def _key(b: AclBinding) -> tuple[object, ...]:
    return (
        b.restype,
        b.name,
        b.resource_pattern_type,
        b.principal,
        b.host,
        b.operation,
        b.permission_type,
    )


def describe_acls(admin: AdminClient, timeout: float) -> list[AclBinding]:
    flt = AclBindingFilter(
        ResourceType.ANY,
        None,  # type: ignore[arg-type]  # None = any name (librdkafka filter semantics)
        ResourcePatternType.ANY,
        None,  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        AclOperation.ANY,
        AclPermissionType.ANY,
    )
    result: list[AclBinding] = admin.describe_acls(flt, request_timeout=timeout).result()
    return result


@dataclass
class AclReport:
    created: int = 0
    existing: int = 0
    extra: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.created > 0


def ensure_acls(admin: AdminClient, catalogue: Catalogue) -> AclReport:
    """Add missing ACLs. Extra ACLs on managed principals are reported, not removed."""
    timeout = catalogue.timeout_s
    wanted = acl_bindings(catalogue)
    have = describe_acls(admin, timeout)
    have_keys = {_key(b) for b in have}
    missing = [b for b in wanted if _key(b) not in have_keys]
    report = AclReport(existing=len(wanted) - len(missing))
    if missing:
        for fut in admin.create_acls(missing, request_timeout=timeout).values():
            fut.result()
        report.created = len(missing)
    managed = {p.kafka_principal for p in catalogue.principals}
    wanted_keys = {_key(b) for b in wanted}
    report.extra = [_describe(b) for b in have if b.principal in managed and _key(b) not in wanted_keys]
    return report


def _name(value: object) -> str:
    return str(getattr(value, "name", value))


def _describe(b: AclBinding) -> str:
    kind = f"{_name(b.restype)}:{_name(b.resource_pattern_type)}:{b.name}"
    return f"{_name(b.permission_type)} {b.principal} {_name(b.operation)} {kind}"


# --------------------------------------------------------------------------- DQ-12
def _reaches_ground_truth(b: AclBinding, gt_topics: Iterable[str]) -> bool:
    if b.restype not in (ResourceType.TOPIC, ResourceType.ANY):
        return False
    if b.name == WILDCARD_RESOURCE and b.resource_pattern_type == ResourcePatternType.LITERAL:
        return True
    if b.resource_pattern_type == ResourcePatternType.PREFIXED:
        return any(t.startswith(b.name) for t in gt_topics) or GROUND_TRUTH_PREFIX.startswith(b.name)
    return b.name in set(gt_topics) or b.name.startswith(GROUND_TRUTH_PREFIX)


def isolation_violations(
    bindings: Iterable[AclBinding], broker_config: dict[str, str], catalogue: Catalogue
) -> list[str]:
    """Pure part of :func:`check_isolation`, for unit tests."""
    problems: list[str] = []
    if not broker_config.get("authorizer.class.name"):
        problems.append("broker has no authorizer (authorizer.class.name is empty): ACLs are not enforced")
    if broker_config.get("allow.everyone.if.no.acl.found", "false").lower() != "false":
        problems.append("allow.everyone.if.no.acl.found is not false: unknown principals can read gt.*")
    dss: list[Principal] = [p for p in catalogue.principals if p.is_dss]
    dss_names = {p.kafka_principal for p in dss}
    supers = {s.strip() for s in broker_config.get("super.users", "").split(";") if s.strip()}
    for name in sorted(dss_names & supers):
        problems.append(f"{name} is a super user, so ACLs do not restrict it (FR-TWIN-14)")
    gt_topics = [t.name for t in catalogue.topics if t.is_ground_truth]
    for b in bindings:
        if b.permission_type != AclPermissionType.ALLOW:
            continue
        if b.principal not in dss_names and b.principal != WILDCARD_PRINCIPAL:
            continue
        if _reaches_ground_truth(b, gt_topics):
            problems.append(f"ground truth exposed to a DSS principal: {_describe(b)} (FR-TWIN-14, DQ-12)")
    return problems


def broker_config(admin: AdminClient, timeout: float) -> dict[str, str]:
    brokers = admin.list_topics(timeout=timeout).brokers
    broker_id = str(min(brokers))
    res = ConfigResource(ResourceType.BROKER, broker_id)
    entries = next(iter(admin.describe_configs([res], request_timeout=timeout).values())).result()
    return {k: (e.value or "") for k, e in entries.items()}


def check_isolation(admin: AdminClient, catalogue: Catalogue) -> list[str]:
    """DQ-12 deployment check. An empty list means ground truth is isolated from DSS principals."""
    timeout = catalogue.timeout_s
    return isolation_violations(describe_acls(admin, timeout), broker_config(admin, timeout), catalogue)
