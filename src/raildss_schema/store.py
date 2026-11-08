"""Load and read a network version in PostgreSQL (spec FR-TWIN-02, FR-DATA-04; issue #18).

``replace_network`` writes every record of one ``network_version`` in a single transaction: rows of
that version are deleted first, so loading the same version again is idempotent and other versions
are never touched. ``counts`` returns the number of rows per entity for a version.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from raildss_schema.base import Stamped
from raildss_schema.ddl import is_json_field
from raildss_schema.entities import ENTITIES


def insert(conn: Any, obj: Stamped) -> None:
    """Insert one record. JSONB columns receive the JSON-mode dump, other columns the Python value."""
    model = type(obj)
    py, js = obj.model_dump(mode="python"), obj.model_dump(mode="json")
    cols: list[str] = []
    params: list[str] = []
    values: dict[str, Any] = {}
    for name, field in model.model_fields.items():
        cols.append(name)
        if is_json_field(field):
            params.append(f"CAST(:{name} AS JSONB)")
            values[name] = json.dumps(js[name], sort_keys=True)
        else:
            params.append(f":{name}")
            values[name] = py[name]
    conn.run(f"INSERT INTO {model.__table__} ({', '.join(cols)}) VALUES ({', '.join(params)})", **values)


def replace_network(
    conn: Any, network_version: str, records: Mapping[type[Stamped], Iterable[Stamped]]
) -> dict[str, int]:
    """Replace all rows of ``network_version`` with ``records``; return rows written per table."""
    written: dict[str, int] = {}
    conn.run("BEGIN")
    try:
        for model in reversed(ENTITIES):
            conn.run(f"DELETE FROM {model.__table__} WHERE network_version = :v", v=network_version)
        for model in ENTITIES:
            rows = list(records.get(model, ()))
            for obj in rows:
                if obj.network_version != network_version:
                    raise ValueError(f"{model.__table__}: record stamped {obj.network_version!r}")
                insert(conn, obj)
            written[model.__table__] = len(rows)
        conn.run("COMMIT")
    except BaseException:
        conn.run("ROLLBACK")
        raise
    return written


def counts(conn: Any, network_version: str) -> dict[str, int]:
    """Rows per entity table for one network version."""
    out: dict[str, int] = {}
    for model in ENTITIES:
        [[n]] = conn.run(
            f"SELECT count(*) FROM {model.__table__} WHERE network_version = :v", v=network_version
        )
        out[model.__table__] = int(n)
    return out
