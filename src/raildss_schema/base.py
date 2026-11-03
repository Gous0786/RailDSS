"""Shared building blocks of the canonical schema (spec §6.1, FR-DATA-04; issue #4).

* :class:`Stamped`: base for every persisted entity. Carries the version stamps
  (``network_version``, ``timetable_version``, ``policy_version``, ``rule_catalogue_version``)
  and a ``raw`` mapping for unmapped source fields (spec §6.6).
* :data:`UtcDatetime`: timezone-aware datetimes, normalised to UTC (spec §5.2: store UTC).
* :class:`Location`: a location stored three ways (net element + offset, km chainage, lat/lon).
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, ClassVar, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

LAT_LIMIT = 90.0
LON_LIMIT = 180.0


def _to_utc(value: dt.datetime) -> dt.datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware (store UTC, display IST)")
    return value.astimezone(dt.UTC)


UtcDatetime = Annotated[dt.datetime, AfterValidator(_to_utc)]
"""A timezone-aware datetime converted to UTC. Naive datetimes are rejected."""

WeekMask = Annotated[tuple[bool, bool, bool, bool, bool, bool, bool], Field(description="Monday-first")]
"""Seven booleans, index 0 = Monday (R06 App. B)."""


class SchemaModel(BaseModel):
    """Strict, immutable base for all schema types."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Location(SchemaModel):
    """A location in three references at once (spec §6.1).

    At least one of ``km`` or (``ne_id`` + ``offset_m``) is required. ``lat``/``lon`` come
    together or not at all.
    """

    ne_id: str | None = None
    offset_m: float | None = Field(default=None, ge=0)
    km: float | None = None
    lat: float | None = Field(default=None, ge=-LAT_LIMIT, le=LAT_LIMIT)
    lon: float | None = Field(default=None, ge=-LON_LIMIT, le=LON_LIMIT)

    @model_validator(mode="after")
    def _check(self) -> Self:
        if self.km is None and (self.ne_id is None or self.offset_m is None):
            raise ValueError("location needs km or (ne_id and offset_m)")
        if (self.lat is None) != (self.lon is None):
            raise ValueError("lat and lon must be given together")
        return self


class Stamped(SchemaModel):
    """Base for persisted entities: version stamps (FR-DATA-04) and raw source attributes."""

    __table__: ClassVar[str]
    __pk__: ClassVar[tuple[str, ...]]

    network_version: str = Field(min_length=1)
    timetable_version: str | None = None
    policy_version: str | None = None
    rule_catalogue_version: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict, description="unmapped source fields (spec §6.6)")


VERSION_STAMPS: tuple[str, ...] = (
    "network_version",
    "timetable_version",
    "policy_version",
    "rule_catalogue_version",
)
