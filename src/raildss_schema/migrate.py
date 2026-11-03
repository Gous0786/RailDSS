"""Minimal SQL migration runner (issue #4).

Migrations live in ``migrations/NNNN_name.up.sql`` / ``.down.sql`` at the repository root.
Applied versions are tracked in ``schema_migrations``. Statements are separated by a blank
line after ``;`` (the generator writes them that way), so no SQL parser is needed.

    python -m raildss_schema migrate up   --url postgresql://user:pw@localhost:5432/raildss
    python -m raildss_schema migrate down --url ...
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import unquote, urlparse

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
NAME_RE = re.compile(r"^(?P<version>\d{4})_(?P<name>[a-z0-9_]+)\.(?P<direction>up|down)\.sql$")
TRACKING = (
    "CREATE TABLE IF NOT EXISTS schema_migrations "
    "(version TEXT PRIMARY KEY, name TEXT NOT NULL, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
)


class Runner(Protocol):
    def run(self, sql: str, **params: Any) -> Any: ...


def connect(url: str) -> Any:
    """Open a pg8000 native connection from a ``postgresql://`` URL."""
    import pg8000.native

    u = urlparse(url)
    return pg8000.native.Connection(
        user=unquote(u.username or ""),
        password=unquote(u.password or "") or None,
        host=u.hostname or "localhost",
        port=u.port or 5432,
        database=(u.path or "/").lstrip("/") or None,
    )


def statements(sql: str) -> list[str]:
    body = "\n".join(line for line in sql.splitlines() if not line.lstrip().startswith("--"))
    return [s.strip() for s in re.split(r";\s*\n\s*\n", body + "\n\n") if s.strip()]


def available(directory: Path = MIGRATIONS_DIR) -> list[tuple[str, str]]:
    found = {}
    for p in directory.glob("*.sql"):
        m = NAME_RE.match(p.name)
        if m:
            found[m["version"]] = m["name"]
    return sorted(found.items())


def applied(conn: Runner) -> list[str]:
    conn.run(TRACKING)
    return [r[0] for r in conn.run("SELECT version FROM schema_migrations ORDER BY version")]


def up(conn: Runner, directory: Path = MIGRATIONS_DIR) -> list[str]:
    done = set(applied(conn))
    newly: list[str] = []
    for version, name in available(directory):
        if version in done:
            continue
        conn.run("BEGIN")
        for stmt in statements((directory / f"{version}_{name}.up.sql").read_text(encoding="utf-8")):
            conn.run(stmt)
        conn.run("INSERT INTO schema_migrations (version, name) VALUES (:v, :n)", v=version, n=name)
        conn.run("COMMIT")
        newly.append(version)
    return newly


def down(conn: Runner, directory: Path = MIGRATIONS_DIR, steps: int | None = None) -> list[str]:
    names = dict(available(directory))
    reverted: list[str] = []
    for version in sorted(applied(conn), reverse=True)[:steps]:
        conn.run("BEGIN")
        for stmt in statements(
            (directory / f"{version}_{names[version]}.down.sql").read_text(encoding="utf-8")
        ):
            conn.run(stmt)
        conn.run("DELETE FROM schema_migrations WHERE version = :v", v=version)
        conn.run("COMMIT")
        reverted.append(version)
    return reverted
