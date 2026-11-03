"""Command line for the canonical schema.

python -m raildss_schema generate            # write migrations/0001_* and schemas/*.json
python -m raildss_schema check               # exit 1 if committed artefacts are stale
python -m raildss_schema migrate up|down --url postgresql://...
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from raildss_schema import ddl, migrate
from raildss_schema.entities import ENTITIES

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = ROOT / "schemas"
MIGRATION = "0001_canonical_schema"


def artefacts() -> dict[Path, str]:
    """Every generated file and its expected content."""
    out: dict[Path, str] = {
        migrate.MIGRATIONS_DIR / f"{MIGRATION}.up.sql": ddl.up_sql(),
        migrate.MIGRATIONS_DIR / f"{MIGRATION}.down.sql": ddl.down_sql(),
    }
    for model in ENTITIES:
        schema = model.model_json_schema(mode="validation")
        out[SCHEMAS_DIR / f"{model.__table__}.schema.json"] = (
            json.dumps(schema, indent=2, sort_keys=True) + "\n"
        )
    return out


def stale() -> list[str]:
    problems: list[str] = []
    expected = artefacts()
    for path, content in expected.items():
        if not path.exists():
            problems.append(f"missing {path.relative_to(ROOT).as_posix()}")
        elif path.read_text(encoding="utf-8") != content:
            problems.append(f"stale {path.relative_to(ROOT).as_posix()}")
    extra = set(SCHEMAS_DIR.glob("*.schema.json")) - set(expected)
    problems += [f"unexpected {p.relative_to(ROOT).as_posix()}" for p in sorted(extra)]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_schema")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("generate", help="write generated migrations and JSON Schemas")
    sub.add_parser("check", help="fail if generated artefacts are stale")
    p_mig = sub.add_parser("migrate", help="apply or revert migrations")
    p_mig.add_argument("direction", choices=["up", "down"])
    p_mig.add_argument("--url", default=os.environ.get("RAILDSS_DB_URL"))
    args = ap.parse_args(argv)

    if args.cmd == "generate":
        for path, content in artefacts().items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
        print(f"wrote {len(artefacts())} files")
        return 0
    if args.cmd == "check":
        problems = stale()
        for p in problems:
            print(f"FAIL  {p}  (run: python -m raildss_schema generate)")
        print("schema artefacts:", "STALE" if problems else "OK")
        return 1 if problems else 0
    if not args.url:
        ap.error("--url or RAILDSS_DB_URL is required")
    conn = migrate.connect(args.url)
    try:
        done = migrate.up(conn) if args.direction == "up" else migrate.down(conn)
    finally:
        conn.close()
    print(f"migrate {args.direction}: {', '.join(done) or 'nothing to do'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
