"""Topic catalogue, partition keys and static ground-truth isolation (spec §7.1-7.2, FR-TWIN-14, DQ-12)."""

from __future__ import annotations

import ast
from typing import Any

import pytest
import yaml
from confluent_kafka.admin import AclOperation, AclPermissionType, ResourcePatternType, ResourceType

from raildss_common.admin import acl_bindings, isolation_violations
from raildss_common.envelope import parse_event
from raildss_common.topics import (
    DEFAULT_CATALOGUE,
    REPO,
    CatalogueError,
    load_catalogue,
    parse_catalogue,
    partition_key,
)
from tests.common.events import envelope, valid_fixtures

SPEC_TOPICS = {
    # §7.1
    "obs.position", "obs.station", "obs.occupancy", "obs.block", "obs.heartbeat",
    "ops.tsr", "ops.blocks", "ops.disruptions", "ops.rakes", "ops.crew", "ops.weather",
    "gt.state", "gt.occupancy", "gt.signal", "gt.delay",
    # §7.2
    "dss.state", "dss.predictions", "dss.conflicts", "dss.candidates", "dss.verification",
    "dss.recommendations", "dss.explanations", "dss.alerts", "dss.kpi",
    "ctrl.decisions", "ctrl.orders", "ctrl.order_acks", "audit.log",
}  # fmt: skip


def _doc() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(DEFAULT_CATALOGUE.read_text(encoding="utf-8"))
    return doc


def test_catalogue_covers_spec_topics() -> None:
    cat = load_catalogue()
    names = {t.name for t in cat.topics}
    assert names >= SPEC_TOPICS
    assert {t.family for t in cat.topics} == {"obs", "ops", "gt", "dss", "ctrl", "audit", "dlq"}
    assert all(t.partitions > 1 for t in cat.topics)
    assert cat.topic("obs.position").dlq == "dlq.obs"
    assert cat.topic("gt.state").dlq is None


def test_fixture_topics_exist_and_section_is_default_key() -> None:
    cat = load_catalogue()
    for _, topic, event in valid_fixtures():
        t = cat.topic(topic)
        assert t.key == "section_id"
        assert partition_key(t, parse_event(event)) == b"KTV-PSA"


def test_payload_key_per_spec_7_2() -> None:
    t = load_catalogue().topic("dss.candidates")
    ev = parse_event(envelope("CANDIDATE", "DSS.DS-6", {"rec_id": "REC-9", "option_id": "OPT-1"}))
    assert partition_key(t, ev) == b"REC-9"
    with pytest.raises(KeyError):
        partition_key(t, parse_event(envelope("CANDIDATE", "DSS.DS-6", {})))


def test_dss_principals_have_no_ground_truth_access() -> None:
    cat = load_catalogue()
    dss = [p for p in cat.principals if p.is_dss]
    assert dss
    for p in dss:
        for t in cat.topics:
            if t.is_ground_truth:
                assert not cat.can_read(p.name, t.name)
    assert cat.can_read("evaluation", "gt.state")
    assert not [b for b in acl_bindings(cat) if b.principal == "User:dss" and b.name.startswith("gt")]
    bindings = acl_bindings(cat)
    assert len({(b.restype, b.name, b.principal, b.operation) for b in bindings}) == len(bindings)


@pytest.mark.parametrize(("op", "grant"), [("read", "gt."), ("read", "gt.state"), ("write", "gt.delay")])
def test_catalogue_granting_dss_ground_truth_is_rejected(op: str, grant: str) -> None:
    doc = _doc()
    dss = next(p for p in doc["principals"] if p["role"] == "dss")
    dss.setdefault(op, []).append(grant)
    with pytest.raises(CatalogueError, match="DQ-12"):
        parse_catalogue(doc)


def test_unknown_topic_grant_and_family_are_rejected() -> None:
    doc = _doc()
    doc["principals"][0]["read"].append("obs.nonexistent")
    with pytest.raises(CatalogueError, match="matches no topic"):
        parse_catalogue(doc)
    doc = _doc()
    doc["topics"].append({"name": "misc.things"})
    with pytest.raises(CatalogueError, match="unknown family"):
        parse_catalogue(doc)


class _Binding:
    """Duck-typed AclBinding for the pure isolation check."""

    def __init__(self, principal: str, name: str, prefixed: bool) -> None:
        self.principal, self.name, self.host = principal, name, "*"
        self.restype = ResourceType.TOPIC
        self.resource_pattern_type = ResourcePatternType.PREFIXED if prefixed else ResourcePatternType.LITERAL
        self.operation, self.permission_type = AclOperation.READ, AclPermissionType.ALLOW


SECURE = {
    "authorizer.class.name": "org.apache.kafka.metadata.authorizer.StandardAuthorizer",
    "allow.everyone.if.no.acl.found": "false",
    "super.users": "User:admin;User:ANONYMOUS",
}


def test_isolation_check_flags_every_exposure() -> None:
    cat = load_catalogue()
    ok: list[Any] = [_Binding("User:dss", "obs.", True), _Binding("User:evaluation", "gt.", True)]
    assert isolation_violations(ok, SECURE, cat) == []
    for bad in (
        _Binding("User:dss", "gt.state", False),
        _Binding("User:dss", "g", True),
        _Binding("User:*", "*", False),
    ):
        assert isolation_violations([bad], SECURE, cat)  # type: ignore[list-item]
    assert isolation_violations([], {**SECURE, "authorizer.class.name": ""}, cat)
    assert isolation_violations([], {**SECURE, "allow.everyone.if.no.acl.found": "true"}, cat)
    assert isolation_violations([], {**SECURE, "super.users": "User:admin;User:dss"}, cat)


DSS_PACKAGES = ["raildss_optimiser", "raildss_verifier"]


def test_no_ground_truth_topic_in_dss_code() -> None:
    """FR-TWIN-14: static analysis finds no gt.* topic name in DSS service code."""
    hits: list[str] = []
    for pkg in DSS_PACKAGES:
        for py in sorted((REPO / "src" / pkg).rglob("*.py")):
            for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value.startswith("gt.")
                ):
                    hits.append(f"{py.relative_to(REPO).as_posix()}:{node.lineno}")
    assert hits == []
