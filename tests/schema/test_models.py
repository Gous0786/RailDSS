"""Canonical schema tests that need no database (issue #4)."""

from __future__ import annotations

import datetime as dt

import pytest
from pydantic import ValidationError

from raildss_schema import ENTITIES, VERSION_STAMPS, Location
from raildss_schema.__main__ import stale
from raildss_schema.ddl import create_table, down_sql, up_sql
from raildss_schema.entities import FreightLoad, OperationalPoint, Service
from raildss_schema.migrate import statements
from tests.schema.fixtures import NV, all_fixtures


def test_every_entity_has_a_fixture() -> None:
    assert {type(f) for f in all_fixtures()} == set(ENTITIES)


@pytest.mark.parametrize("model", ENTITIES, ids=lambda m: m.__name__)
def test_entity_carries_version_stamps(model: type) -> None:
    for stamp in VERSION_STAMPS:
        assert stamp in model.model_fields, f"{model.__name__} lacks {stamp}"
    assert "raw" in model.model_fields


@pytest.mark.parametrize("model", ENTITIES, ids=lambda m: m.__name__)
def test_primary_key_is_valid(model: type) -> None:
    assert model.__pk__[0] == "network_version"
    assert set(model.__pk__) <= set(model.model_fields)
    assert model.__table__.isidentifier() and model.__table__.islower()


@pytest.mark.parametrize("fixture", all_fixtures(), ids=lambda f: type(f).__name__)
def test_json_round_trip_is_lossless(fixture: object) -> None:
    model = type(fixture)
    assert model.model_validate_json(fixture.model_dump_json()) == fixture  # type: ignore[attr-defined]


def test_datetimes_are_stored_in_utc() -> None:
    ist = dt.timezone(dt.timedelta(hours=5, minutes=30))
    load = next(f for f in all_fixtures() if isinstance(f, FreightLoad))
    moved = load.model_copy(update={}).model_validate(
        {**load.model_dump(), "entry_time": dt.datetime(2025, 8, 20, 9, 40, tzinfo=ist)}
    )
    assert moved.entry_time == dt.datetime(2025, 8, 20, 4, 10, tzinfo=dt.UTC)
    assert moved.entry_time.utcoffset() == dt.timedelta(0)


def test_naive_datetime_is_rejected() -> None:
    data = next(f for f in all_fixtures() if isinstance(f, FreightLoad)).model_dump()
    data["entry_time"] = dt.datetime(2025, 8, 20, 4, 10)
    with pytest.raises(ValidationError, match="timezone-aware"):
        FreightLoad.model_validate(data)


def test_location_needs_km_or_net_element() -> None:
    assert Location(km=12.5).km == 12.5
    assert Location(ne_id="NE-1", offset_m=10.0).ne_id == "NE-1"
    with pytest.raises(ValidationError):
        Location(lat=18.0, lon=83.0)
    with pytest.raises(ValidationError):
        Location(km=1.0, lat=18.0)


def test_week_mask_has_seven_days() -> None:
    data = next(f for f in all_fixtures() if isinstance(f, Service)).model_dump()
    data["days"] = (True,) * 6
    with pytest.raises(ValidationError):
        Service.model_validate(data)


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        OperationalPoint.model_validate({"network_version": NV, "code": "X", "colour": "red"})


def test_ddl_has_a_column_per_field_and_postgis_geom() -> None:
    for model in ENTITIES:
        table = create_table(model)
        for name in model.model_fields:
            assert f"\n    {name} " in table, f"{model.__table__}.{name} missing"
    assert "geom geometry(Point, 4326)" in create_table(OperationalPoint)
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in up_sql()


def test_migration_statements_split_cleanly() -> None:
    assert len(statements(up_sql())) == 1 + len(ENTITIES)
    assert len(statements(down_sql())) == len(ENTITIES)


def test_committed_artefacts_are_up_to_date() -> None:
    assert stale() == [], "run: python -m raildss_schema generate"
