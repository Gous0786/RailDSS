"""Versioned real-time event envelope (spec §6.2, §7.1; DQ-01; issue #19).

Every message on the bus is one :class:`EventEnvelope` serialised as JSON. Version ``1.0``
is the envelope of spec §6.2 plus ``loco_id`` (shown in the §6.2 example). It is published
as a JSON Schema (draft 2020-12) at ``schemas/events/envelope-1.0.schema.json``, generated
from these models by ``python -m raildss_common schema generate``.

Validation is strict: unknown top-level fields, unknown event types, other schema versions,
naive timestamps and type coercions (``"74"`` for a number, ``"yes"`` for a boolean) are all
rejected. Event types with a payload sketch in spec §7.1 have a payload model, so their payload
is checked too. The other payloads (interlocking states, ``MaintBlock``, the internal ``dss.*``
records) are any JSON object until the issue that owns them adds a model; that is a minor
version bump.

Typical use::

    from raildss_common.envelope import parse_event

    event = parse_event(raw_bytes)          # raises EnvelopeError with the reason (-> DLQ)
    event.event_type, event.typed_payload()
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from enum import StrEnum
from typing import Annotated, Any, ClassVar, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    Strict,
    ValidationError,
    model_validator,
)
from pydantic.json_schema import models_json_schema

from raildss_schema import Location, UtcDatetime
from raildss_schema.base import LAT_LIMIT, LON_LIMIT

SCHEMA_VERSION = "1.0"
SCHEMA_ID = f"https://github.com/Gous0786/RailDSS/schemas/events/envelope-{SCHEMA_VERSION}.schema.json"
FULL_CIRCLE_DEG = 360.0

_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
SOURCE_PATTERN = r"^[A-Z][A-Z0-9_-]*(\.[A-Z0-9_-]+)*$"
SECTION_PATTERN = r"^[A-Z][A-Z0-9]*(-[A-Z0-9]+)*$"
TOKEN_PATTERN = r"^\S+$"
STATION_PATTERN = r"^[A-Z]{1,6}$"
BLOCK_SECTION_PATTERN = r"^[A-Z]{1,6}-[A-Z]{1,6}$"


class EnvelopeError(ValueError):
    """An event failed envelope validation (DQ-01). ``reason`` is a one-line summary."""

    def __init__(self, reason: str, errors: list[str] | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.errors = errors or []


# --------------------------------------------------------------------------- scalar types
def _canonical_uuid(value: Any) -> Any:
    if isinstance(value, str) and not _UUID_RE.match(value):
        raise ValueError("must be a canonical hyphenated UUID string")
    return value


def _timestamp_text(value: Any) -> Any:
    if not isinstance(value, str | dt.datetime):
        raise ValueError("must be an RFC 3339 date-time string with a UTC offset")
    return value


EventId = Annotated[UUID, BeforeValidator(_canonical_uuid)]
"""A UUID in canonical 8-4-4-4-12 form (what the JSON Schema ``uuid`` format accepts)."""

EventTime = Annotated[UtcDatetime, BeforeValidator(_timestamp_text)]
"""A timezone-aware RFC 3339 timestamp, normalised to UTC. Epoch numbers and naive times fail."""

Number = Annotated[float, Strict()]
"""A JSON number (int or float). Strings are not coerced."""
Count = Annotated[int, Strict()]
Flag = Annotated[bool, Strict()]
Token = Annotated[str, Field(pattern=TOKEN_PATTERN)]
"""A non-empty identifier without whitespace."""


class EventModel(BaseModel):
    """Strict, immutable base for envelope parts."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class EventType(StrEnum):
    """Closed list of event types in envelope 1.0 (spec §6.2 table, §7.1 and §7.2)."""

    # Simulated external feeds (spec §6.2, §7.1)
    TRAIN_POSITION = "TRAIN_POSITION"
    STATION_EVENT = "STATION_EVENT"
    TDS_OCCUPANCY = "TDS_OCCUPANCY"
    SIGNAL_ASPECT = "SIGNAL_ASPECT"
    POINT_POSITION = "POINT_POSITION"
    ROUTE_SET = "ROUTE_SET"
    ROUTE_RELEASED = "ROUTE_RELEASED"
    LINE_CLEAR = "LINE_CLEAR"
    TRAIN_OUT_OF_SECTION = "TRAIN_OUT_OF_SECTION"
    TSR_ISSUED = "TSR_ISSUED"
    TSR_WITHDRAWN = "TSR_WITHDRAWN"
    MAINT_BLOCK = "MAINT_BLOCK"
    DISRUPTION_RAISED = "DISRUPTION_RAISED"
    DISRUPTION_UPDATED = "DISRUPTION_UPDATED"
    DISRUPTION_CLEARED = "DISRUPTION_CLEARED"
    RAKE_READY = "RAKE_READY"
    CREW_STATUS = "CREW_STATUS"
    WEATHER = "WEATHER"
    CONTROL_ORDER = "CONTROL_ORDER"
    ORDER_ACK = "ORDER_ACK"
    CONTROLLER_ACTION = "CONTROLLER_ACTION"
    HEARTBEAT = "HEARTBEAT"
    # Ground truth (gt.*, evaluation only; FR-TWIN-14)
    TRAIN_STATE = "TRAIN_STATE"
    DELAY_RECORD = "DELAY_RECORD"
    # Internal DSS records (spec §7.2)
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    PREDICTIONS = "PREDICTIONS"
    CONFLICTS = "CONFLICTS"
    CANDIDATE = "CANDIDATE"
    VERIFICATION = "VERIFICATION"
    RECOMMENDATION = "RECOMMENDATION"
    EXPLANATION = "EXPLANATION"
    ALERT = "ALERT"
    KPI_SNAPSHOT = "KPI_SNAPSHOT"
    AUDIT_RECORD = "AUDIT_RECORD"


class EventLocation(Location):
    """Location of §6.2: the canonical :class:`raildss_schema.Location` plus GPS accuracy."""

    model_config = ConfigDict(
        json_schema_extra={
            "anyOf": [
                {"required": ["km"], "properties": {"km": {"type": "number"}}},
                {
                    "required": ["ne_id", "offset_m"],
                    "properties": {"ne_id": {"type": "string"}, "offset_m": {"type": "number"}},
                },
            ],
            "dependentRequired": {"lat": ["lon"], "lon": ["lat"]},
        }
    )

    ne_id: Token | None = None
    offset_m: Number | None = Field(default=None, ge=0)
    km: Number | None = None
    lat: Number | None = Field(default=None, ge=-LAT_LIMIT, le=LAT_LIMIT)
    lon: Number | None = Field(default=None, ge=-LON_LIMIT, le=LON_LIMIT)
    accuracy_m: Number | None = Field(default=None, ge=0)


class Quality(EventModel):
    """Data-quality flags (DQ-02, DQ-03). ``duplicate_of`` names the first copy of a duplicate."""

    simulated_noise: Flag = False
    late: Flag = False
    duplicate_of: EventId | None = None


# --------------------------------------------------------------------------- payloads (§7.1)
class PositionPayload(EventModel):
    """``TRAIN_POSITION`` on ``obs.position`` (RTIS-like, 30 s)."""

    speed_kmh: Number = Field(ge=0)
    heading_deg: Number | None = Field(default=None, ge=0, lt=FULL_CIRCLE_DEG)


class StationEventPayload(EventModel):
    """``STATION_EVENT`` on ``obs.station`` (COA-like; minute resolution at manual stations)."""

    station: str = Field(pattern=STATION_PATTERN)
    kind: Literal["ARR", "DEP", "PASS"]
    line_used: Token | None = None
    manual: Flag
    reported_time: EventTime | None = None


class OccupancyPayload(EventModel):
    """``TDS_OCCUPANCY`` on ``obs.occupancy`` (logger / EI)."""

    tds_id: str = Field(min_length=1)
    occupied: Flag


class LineClearPayload(EventModel):
    """``LINE_CLEAR`` on ``obs.block`` (block instruments)."""

    block_section: str = Field(pattern=BLOCK_SECTION_PATTERN)
    line: Token | None = None
    kind: Literal["LINE_CLEAR_REQ", "LINE_CLEAR_GRANTED", "LINE_CLEAR_CANCELLED"]
    train_run_id: Token | None = None


class TrainOutOfSectionPayload(EventModel):
    """``TRAIN_OUT_OF_SECTION`` on ``obs.block``."""

    block_section: str = Field(pattern=BLOCK_SECTION_PATTERN)
    line: Token | None = None
    kind: Literal["TRAIN_OUT_OF_SECTION"] = "TRAIN_OUT_OF_SECTION"
    train_run_id: Token | None = None


class TsrIssuedPayload(EventModel):
    """``TSR_ISSUED`` on ``ops.tsr`` (caution-order register)."""

    tsr_id: Token
    km_from: Number
    km_to: Number
    vmax_kmh: Number = Field(gt=0)
    reason: str | None = None
    valid_from: EventTime
    valid_to: EventTime | None = None
    notice_station: str | None = Field(default=None, pattern=STATION_PATTERN)
    caution_order_no: Token | None = None


class TsrWithdrawnPayload(EventModel):
    """``TSR_WITHDRAWN`` on ``ops.tsr``."""

    tsr_id: Token
    caution_order_no: Token | None = None


class _DisruptionPayload(EventModel):
    incident_id: Token
    type: Token
    cause_code: Token | None = None
    location: EventLocation | None = None
    etr: EventTime | None = None
    etr_source: Token | None = None
    etr_sigma_min: Number | None = Field(default=None, ge=0)
    lines_affected: list[Token] = Field(default_factory=list)


class DisruptionRaisedPayload(_DisruptionPayload):
    """``DISRUPTION_RAISED`` on ``ops.disruptions``. The ETR is an estimate, not truth."""

    status: Literal["RAISED"]


class DisruptionUpdatedPayload(_DisruptionPayload):
    """``DISRUPTION_UPDATED`` on ``ops.disruptions``."""

    status: Literal["UPDATED"]


class DisruptionClearedPayload(_DisruptionPayload):
    """``DISRUPTION_CLEARED`` on ``ops.disruptions``."""

    status: Literal["CLEARED"]


class RakeReadyPayload(EventModel):
    """``RAKE_READY`` on ``ops.rakes`` (FOIS RMS-like)."""

    rake_id: Token
    load_id: Token | None = None
    type: Token
    loaded: Flag
    commodity: Token | None = None
    entry_point: str = Field(pattern=STATION_PATTERN)
    ready_time: EventTime
    consist: dict[str, Any] | None = None


class CrewStatusPayload(EventModel):
    """``CREW_STATUS`` on ``ops.crew`` (CMS-like). The duty limit comes from the parameter register."""

    crew_duty_id: Token
    train_run_id: Token | None = None
    sign_on: EventTime
    duty_limit_min: Number = Field(gt=0)


class WeatherPayload(EventModel):
    """``WEATHER`` on ``ops.weather`` (IMD-like, P2)."""

    rain_mm_h: Number | None = Field(default=None, ge=0)
    visibility_m: Number | None = Field(default=None, ge=0)


class HeartbeatPayload(EventModel):
    """``HEARTBEAT`` on ``obs.heartbeat`` (feed health, 10 s per source)."""

    source: str = Field(pattern=SOURCE_PATTERN)
    lag_s: Number = Field(ge=0)
    count_10s: Count = Field(ge=0)


PAYLOADS: dict[EventType, type[EventModel]] = {
    EventType.TRAIN_POSITION: PositionPayload,
    EventType.STATION_EVENT: StationEventPayload,
    EventType.TDS_OCCUPANCY: OccupancyPayload,
    EventType.LINE_CLEAR: LineClearPayload,
    EventType.TRAIN_OUT_OF_SECTION: TrainOutOfSectionPayload,
    EventType.TSR_ISSUED: TsrIssuedPayload,
    EventType.TSR_WITHDRAWN: TsrWithdrawnPayload,
    EventType.DISRUPTION_RAISED: DisruptionRaisedPayload,
    EventType.DISRUPTION_UPDATED: DisruptionUpdatedPayload,
    EventType.DISRUPTION_CLEARED: DisruptionClearedPayload,
    EventType.RAKE_READY: RakeReadyPayload,
    EventType.CREW_STATUS: CrewStatusPayload,
    EventType.WEATHER: WeatherPayload,
    EventType.HEARTBEAT: HeartbeatPayload,
}
"""Payload model per event type. Types not listed accept any JSON object in 1.0."""

REQUIRED_CONTEXT: dict[EventType, tuple[str, ...]] = {
    EventType.TRAIN_POSITION: ("train_run_id", "location"),
    EventType.STATION_EVENT: ("train_run_id",),
    EventType.TRAIN_STATE: ("train_run_id", "location"),
}
"""Envelope fields that must be non-null for an event type (a position needs a train and a place)."""


# --------------------------------------------------------------------------- envelope
class EventEnvelope(EventModel):
    """The §6.2 envelope. ``section_id`` is the default partition key (NFR-SCL-08)."""

    VERSION: ClassVar[str] = SCHEMA_VERSION

    event_id: EventId
    event_type: EventType
    schema_version: Literal["1.0"]
    source: str = Field(pattern=SOURCE_PATTERN, description="Producing system, e.g. SIM.RTIS")
    section_id: str = Field(pattern=SECTION_PATTERN, description="Partition key, e.g. KTV-PSA")
    network_version: Token
    scenario_id: Token | None = Field(default=None, description="Null in live operation")
    seq: Count = Field(ge=0, description="Per-source sequence number (DQ-03)")
    event_time: EventTime
    ingest_time: EventTime | None = Field(default=None, description="Set when the feed delivered it")
    train_run_id: Token | None = None
    loco_id: Token | None = None
    location: EventLocation | None = None
    payload: dict[str, Any]
    quality: Quality = Field(default_factory=Quality)

    @model_validator(mode="after")
    def _check_type(self) -> Self:
        missing = [f for f in REQUIRED_CONTEXT.get(self.event_type, ()) if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.event_type} needs {', '.join(missing)}")
        model = PAYLOADS.get(self.event_type)
        if model is not None:
            try:
                model.model_validate(self.payload)
            except ValidationError as exc:
                raise ValueError(f"payload invalid for {self.event_type}: {_summary(exc)}") from None
        return self

    def typed_payload(self) -> EventModel | dict[str, Any]:
        """The payload as its model, or the plain mapping when the type has no model yet."""
        model = PAYLOADS.get(self.event_type)
        return model.model_validate(self.payload) if model is not None else dict(self.payload)

    def to_json(self) -> bytes:
        """Wire form (UTF-8 JSON). Round-trips through :func:`parse_event`."""
        return self.model_dump_json().encode("utf-8")


def _summary(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors())


def parse_event(raw: bytes | str | Mapping[str, Any]) -> EventEnvelope:
    """Decode and validate one event (DQ-01). Raises :class:`EnvelopeError` with the reason."""
    if isinstance(raw, bytes | str):
        try:
            data = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EnvelopeError(f"not valid JSON: {exc}") from None
    else:
        data = raw
    if not isinstance(data, Mapping):
        raise EnvelopeError(f"envelope must be a JSON object, got {type(data).__name__}")
    version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise EnvelopeError(f"unsupported schema_version {version!r} (supported: {SCHEMA_VERSION})")
    try:
        return EventEnvelope.model_validate(data)
    except ValidationError as exc:
        errors = [f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()]
        raise EnvelopeError(f"envelope invalid: {errors[0]}", errors) from None


# --------------------------------------------------------------------------- JSON Schema
def json_schema() -> dict[str, Any]:
    """The published JSON Schema (draft 2020-12) for envelope :data:`SCHEMA_VERSION`.

    The per-type rules (:data:`PAYLOADS`, :data:`REQUIRED_CONTEXT`) become ``if``/``then``
    branches, so a generic JSON Schema validator enforces the same rules as :func:`parse_event`.
    """
    models: list[type[BaseModel]] = [EventEnvelope, *dict.fromkeys(PAYLOADS.values())]
    _, top = models_json_schema([(m, "validation") for m in models], ref_template="#/$defs/{model}")
    defs: dict[str, Any] = top["$defs"]
    root = defs.pop(EventEnvelope.__name__)
    branches: list[dict[str, Any]] = []
    for event_type in EventType:
        then: dict[str, Any] = {}
        model = PAYLOADS.get(event_type)
        if model is not None:
            then["properties"] = {"payload": {"$ref": f"#/$defs/{model.__name__}"}}
        needed = REQUIRED_CONTEXT.get(event_type, ())
        if needed:
            then["required"] = list(needed)
            then.setdefault("properties", {}).update({f: {"not": {"type": "null"}} for f in needed})
        if then:
            branches.append(
                {
                    "if": {"properties": {"event_type": {"const": event_type.value}}},
                    "then": then,
                }
            )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        **root,
        "title": f"RailDSS event envelope {SCHEMA_VERSION}",
        "allOf": branches,
        "$defs": defs,
    }
