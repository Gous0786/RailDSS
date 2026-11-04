"""Envelope validation (spec §6.2, §7.1, DQ-01; issue #19).

Every fixture is checked twice: by ``parse_event`` (what services use) and by a generic
JSON Schema validator against the committed ``schemas/events/envelope-1.0.schema.json``,
so the published schema and the Python models cannot drift apart.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from raildss_common.__main__ import SCHEMA_PATH, schema_text
from raildss_common.envelope import EnvelopeError, EventType, PositionPayload, parse_event
from tests.common.events import MALFORMED, SPEC_6_2_EXAMPLE, valid_fixtures

SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
VALIDATOR = Draft202012Validator(SCHEMA, format_checker=Draft202012Validator.FORMAT_CHECKER)
VALID = valid_fixtures()


def test_committed_schema_is_current() -> None:
    assert SCHEMA_PATH.read_text(encoding="utf-8") == schema_text(), (
        "run: python -m raildss_common schema generate"
    )
    Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize(("name", "topic", "event"), VALID, ids=[v[0] for v in VALID])
def test_spec_examples_validate(name: str, topic: str, event: dict[str, Any]) -> None:
    parsed = parse_event(json.dumps(event).encode())
    assert parsed.event_type == event["event_type"]
    assert [e.message for e in VALIDATOR.iter_errors(event)] == []
    # Round trip through the wire form; the wire form is schema-valid too.
    wire = parsed.to_json()
    assert parse_event(wire) == parsed
    assert [e.message for e in VALIDATOR.iter_errors(json.loads(wire))] == []


@pytest.mark.parametrize(("name", "event"), MALFORMED, ids=[m[0] for m in MALFORMED])
def test_malformed_fixtures_are_rejected(name: str, event: dict[str, Any]) -> None:
    with pytest.raises(EnvelopeError) as exc:
        parse_event(json.dumps(event).encode())
    assert exc.value.reason
    assert list(VALIDATOR.iter_errors(event)), f"JSON Schema accepted malformed fixture {name}"


@pytest.mark.parametrize("raw", [b"not json", b"[1, 2]", b"\xff\xfe", b"null"])
def test_non_envelope_bytes_are_rejected(raw: bytes) -> None:
    with pytest.raises(EnvelopeError):
        parse_event(raw)


def test_typed_payload_and_utc_normalisation() -> None:
    ev = parse_event({**SPEC_6_2_EXAMPLE, "event_time": "2026-11-02T09:42:30+05:30"})
    assert ev.event_time.isoformat() == "2026-11-02T04:12:30+00:00"
    payload = ev.typed_payload()
    assert isinstance(payload, PositionPayload)
    assert payload.speed_kmh == SPEC_6_2_EXAMPLE["payload"]["speed_kmh"]


def test_every_event_type_is_in_the_schema() -> None:
    assert SCHEMA["$defs"]["EventType"]["enum"] == [t.value for t in EventType]
