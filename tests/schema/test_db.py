"""Database tests for the canonical schema (issue #4). Need PostgreSQL + PostGIS.

Run with ``RAILDSS_TEST_DB_URL=postgresql://user:pw@host:5432/db pytest -m db``. Skipped otherwise.
The CI job ``db`` provides a TimescaleDB-HA (PostGIS) service container.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any

import pytest

from raildss_schema import ENTITIES, Stamped
from raildss_schema.ddl import is_json_field
from raildss_schema.migrate import applied, connect, down, up
from tests.schema.fixtures import all_fixtures

URL = os.environ.get("RAILDSS_TEST_DB_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not URL, reason="RAILDSS_TEST_DB_URL not set (database tests run in CI)"),
]


@pytest.fixture()
def conn() -> Iterator[Any]:
    assert URL
    c = connect(URL)
    down(c)  # start clean
    yield c
    down(c)
    c.close()


def _insert(c: Any, obj: Stamped) -> None:
    model = type(obj)
    py, js = obj.model_dump(mode="python"), obj.model_dump(mode="json")
    cols, params, values = [], [], {}
    for name, field in model.model_fields.items():
        cols.append(name)
        if is_json_field(field):
            params.append(f"CAST(:{name} AS JSONB)")
            values[name] = json.dumps(js[name])
        else:
            params.append(f":{name}")
            values[name] = py[name]
    c.run(f"INSERT INTO {model.__table__} ({', '.join(cols)}) VALUES ({', '.join(params)})", **values)


def _select_all(c: Any, model: type[Stamped]) -> list[Stamped]:
    cols = list(model.model_fields)
    rows = c.run(f"SELECT {', '.join(cols)} FROM {model.__table__}")
    out = []
    for row in rows:
        data = {}
        for name, v in zip(cols, row, strict=True):
            data[name] = (
                json.loads(v) if isinstance(v, str) and is_json_field(model.model_fields[name]) else v
            )
        out.append(model.model_validate(data))
    return out


def test_migrations_apply_and_are_reversible(conn: Any) -> None:
    assert up(conn) == ["0001"]
    assert applied(conn) == ["0001"]
    tables = {r[0] for r in conn.run("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert {m.__table__ for m in ENTITIES} <= tables
    assert down(conn) == ["0001"]
    tables = {r[0] for r in conn.run("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
    assert not ({m.__table__ for m in ENTITIES} & tables)
    assert up(conn) == ["0001"]  # re-apply after revert


def test_round_trip_model_db_model_is_lossless(conn: Any) -> None:
    up(conn)
    for fixture in all_fixtures():
        _insert(conn, fixture)
        restored = _select_all(conn, type(fixture))
        assert restored == [fixture], type(fixture).__name__


def test_postgis_geometry_is_generated(conn: Any) -> None:
    up(conn)
    op = next(f for f in all_fixtures() if f.__table__ == "operational_point")
    _insert(conn, op)
    [[wkt]] = conn.run("SELECT ST_AsText(geom) FROM operational_point")
    assert wkt == "POINT(83.57 18.31)"


def test_version_stamp_columns_exist(conn: Any) -> None:
    up(conn)
    for model in ENTITIES:
        cols = {
            r[0]
            for r in conn.run(
                "SELECT column_name FROM information_schema.columns WHERE table_name = :t", t=model.__table__
            )
        }
        assert {"network_version", "timetable_version", "policy_version", "rule_catalogue_version"} <= cols
