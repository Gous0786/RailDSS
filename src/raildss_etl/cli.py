"""The ``raildss`` command line (issue #18).

    raildss network publish [--out DIR] [--check] [--db-url URL]
    raildss network load KTV-PSA-2025-08 --db-url URL [--dir DIR]
    raildss network show KTV-PSA-2025-08 [--dir DIR] [--db-url URL]

``publish`` runs the whole ETL, validates and writes the publication; ``--check`` rebuilds it and
fails on any difference from the committed files (CI gate). ``show`` prints the version stamps and
the record count per entity, from the published files or, with ``--db-url``, from PostgreSQL.
``load`` writes the committed publication into PostgreSQL + PostGIS without re-running the ETL.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from raildss_etl import publish
from raildss_etl.mapping import REPO
from raildss_schema import migrate, store
from raildss_schema.entities import ENTITIES

MAX_DIFF_LINES = 40


def _db_url(arg: str | None) -> str | None:
    return arg or os.environ.get("RAILDSS_DB_URL")


def cmd_publish(args: argparse.Namespace) -> int:
    try:
        assembled = publish.assemble()
    except publish.PublicationBlockedError as e:
        print(f"FAIL  {e}")
        for r in e.report.rules:
            for line in r.errors:
                print(f"      {r.id}: {line}")
        return 1
    files = publish.render(assembled)
    out = args.out or publish.default_dir(assembled.network_version)
    meta_hash = publish.network_hash(files)
    print(f"validator: 0 errors ({', '.join(r.id for r in assembled.validation.rules)})")
    print(f"network {assembled.network_version} hash {meta_hash}")
    if args.check:
        problems = publish.diff(files, out)
        for p in problems[:MAX_DIFF_LINES]:
            print(f"FAIL  {p}")
        print(
            f"network publication: {'DIFFERS' if problems else 'OK'} ({out}); "
            "update it deliberately with `raildss network publish`"
            if problems
            else f"network publication: OK ({out.relative_to(REPO) if out.is_relative_to(REPO) else out})"
        )
        return 1 if problems else 0
    paths = publish.write(files, out)
    print(f"wrote {len(paths)} files to {out}")
    url = _db_url(args.db_url)
    if url:
        conn = migrate.connect(url)
        try:
            migrate.up(conn)
            written = publish.load_database(conn, assembled.tables, assembled.network_version)
        finally:
            conn.close()
        print(f"loaded {sum(written.values())} records into PostgreSQL")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    url = _db_url(args.db_url)
    if not url:
        print("FAIL  --db-url or RAILDSS_DB_URL is required")
        return 1
    version: str = args.version
    out = args.dir or publish.default_dir(version)
    meta, tables, texts = publish.read_export(out)
    if meta["network_version"] != version or publish.network_hash(texts) != meta["network_hash"]:
        print(f"FAIL  {out} is not an intact publication of {version}")
        return 1
    conn = migrate.connect(url)
    try:
        migrate.up(conn)
        written = publish.load_database(conn, tables, version)
    finally:
        conn.close()
    print(f"loaded {sum(written.values())} records of {version} into PostgreSQL")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    version: str = args.version
    out = args.dir or publish.default_dir(version)
    if not (out / publish.NETWORK_FILE).exists():
        print(f"FAIL  network {version} is not published at {out} (run `raildss network publish`)")
        return 1
    meta, tables, texts = publish.read_export(out)
    if meta["network_version"] != version:
        print(f"FAIL  {out} holds {meta['network_version']}, not {version}")
        return 1
    digest = publish.network_hash(texts)
    intact = digest == meta["network_hash"]
    print(f"network_version   {meta['network_version']}")
    print(f"timetable_version {meta['timetable_version']}")
    print(f"network_hash      {meta['network_hash']} ({'verified' if intact else 'MISMATCH'})")
    print(f"validator errors  {meta['validator_errors']}")
    print(
        f"ETL {meta['etl_version']}, mapping {meta['mapping_version']}, register {meta['register_version']}"
    )
    db_counts: dict[str, int] | None = None
    url = _db_url(args.db_url)
    if url:
        conn = migrate.connect(url)
        try:
            db_counts = store.counts(conn, version)
        finally:
            conn.close()
    print()
    header = f"{'layer':<14}{'entity':<22}{'records':>9}" + (f"{'in database':>13}" if db_counts else "")
    print(header)
    for layer, names in publish.LAYERS.items():
        for name in names:
            row = f"{layer:<14}{name:<22}{len(tables[name]):>9}"
            if db_counts is not None:
                row += f"{db_counts[name]:>13}"
            print(row)
    total = sum(len(v) for v in tables.values())
    print(f"{'total':<36}{total:>9}" + (f"{sum(db_counts.values()):>13}" if db_counts else ""))
    assert {m.__table__ for m in ENTITIES} == set(tables)
    return 0 if intact else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="raildss")
    sub = ap.add_subparsers(dest="group", required=True)
    net = sub.add_parser("network", help="network publication").add_subparsers(dest="cmd", required=True)
    p_pub = net.add_parser("publish", help="build, validate and write the network publication")
    p_pub.add_argument("--out", type=Path, default=None)
    p_pub.add_argument("--check", action="store_true", help="fail if the committed publication differs")
    p_pub.add_argument("--db-url", default=None, help="also load into PostgreSQL (or RAILDSS_DB_URL)")
    p_show = net.add_parser("show", help="print version stamps and counts per entity")
    p_show.add_argument("version")
    p_show.add_argument("--dir", type=Path, default=None)
    p_show.add_argument("--db-url", default=None, help="also count rows in PostgreSQL (or RAILDSS_DB_URL)")
    p_load = net.add_parser("load", help="load the published files into PostgreSQL + PostGIS")
    p_load.add_argument("version")
    p_load.add_argument("--dir", type=Path, default=None)
    p_load.add_argument("--db-url", default=None, help="PostgreSQL URL (or RAILDSS_DB_URL)")
    args = ap.parse_args(argv)
    commands = {"publish": cmd_publish, "show": cmd_show, "load": cmd_load}
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
