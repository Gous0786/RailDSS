"""Load the committed network publication into PostgreSQL + PostGIS (issue #18). Needs a database.

Run with ``RAILDSS_TEST_DB_URL=postgresql://user:pw@host:5432/db pytest -m db``; skipped otherwise.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import pytest

from raildss_etl import publish
from raildss_schema import store
from raildss_schema.migrate import connect, down, up

URL = os.environ.get("RAILDSS_TEST_DB_URL")
pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(not URL, reason="RAILDSS_TEST_DB_URL not set (database tests run in CI)"),
]
VERSION = "KTV-PSA-2025-08"


@pytest.fixture()
def conn() -> Iterator[Any]:
    assert URL
    c = connect(URL)
    down(c)
    up(c)
    yield c
    down(c)
    c.close()


def test_published_network_loads_and_reloads_idempotently(conn: Any) -> None:
    meta, tables, _ = publish.read_export(publish.default_dir(VERSION))
    written = publish.load_database(conn, tables, VERSION)
    assert written == meta["counts"]
    assert store.counts(conn, VERSION) == meta["counts"]
    publish.load_database(conn, tables, VERSION)  # replaces, never duplicates
    assert store.counts(conn, VERSION) == meta["counts"]
    with_position = sum(1 for o in tables["operational_point"] if getattr(o, "lat") is not None)  # noqa: B009
    [[n]] = conn.run("SELECT count(geom) FROM operational_point WHERE network_version = :v", v=VERSION)
    assert n == with_position > 0


def test_loading_one_version_leaves_another_untouched(conn: Any) -> None:
    _, tables, _ = publish.read_export(publish.default_dir(VERSION))
    publish.load_database(conn, tables, VERSION)
    other = {
        name: [r.model_copy(update={"network_version": "OTHER-1"}) for r in rows[:3]]
        for name, rows in tables.items()
    }
    publish.load_database(conn, other, "OTHER-1")
    assert store.counts(conn, "OTHER-1")["operational_point"] == 3
    assert store.counts(conn, VERSION)["operational_point"] == len(tables["operational_point"])
