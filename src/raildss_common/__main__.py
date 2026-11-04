"""Command line for the event envelope and the Kafka topology (issue #19).

python -m raildss_common schema generate     # write schemas/events/envelope-<v>.schema.json
python -m raildss_common schema check        # exit 1 if the committed schema is stale
python -m raildss_common kafka lint          # static catalogue checks (incl. DQ-12), no broker
python -m raildss_common kafka apply         # create topics + ACLs (idempotent)
python -m raildss_common kafka apply --expect-no-changes   # exit 1 if anything had to change
python -m raildss_common kafka check         # DQ-12 deployment check against the broker

Broker connection: RAILDSS_KAFKA_BOOTSTRAP, RAILDSS_KAFKA_USERNAME, RAILDSS_KAFKA_PASSWORD
(a super user, ``admin`` in docker-compose.yml).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from raildss_common.envelope import SCHEMA_VERSION, json_schema
from raildss_common.topics import REPO, CatalogueError, load_catalogue

SCHEMA_PATH = REPO / "schemas" / "events" / f"envelope-{SCHEMA_VERSION}.schema.json"
JSON_INDENT = 2


def schema_text() -> str:
    return json.dumps(json_schema(), indent=JSON_INDENT, sort_keys=True, ensure_ascii=False) + "\n"


def _schema(cmd: str) -> int:
    if cmd == "generate":
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(schema_text(), encoding="utf-8", newline="\n")
        print(f"wrote {SCHEMA_PATH.relative_to(REPO).as_posix()}")
        return 0
    ok = SCHEMA_PATH.exists() and SCHEMA_PATH.read_text(encoding="utf-8") == schema_text()
    if not ok:
        print(
            f"FAIL  stale {SCHEMA_PATH.relative_to(REPO).as_posix()}"
            "  (run: python -m raildss_common schema generate)"
        )
    print("envelope schema:", "OK" if ok else "STALE")
    return 0 if ok else 1


def _kafka(args: argparse.Namespace) -> int:
    try:
        catalogue = load_catalogue(args.catalogue)
    except CatalogueError as exc:
        print(f"FAIL  {exc}")
        return 1
    if args.cmd == "lint":
        print(f"kafka catalogue: OK ({len(catalogue.topics)} topics, {len(catalogue.principals)} principals)")
        return 0

    from raildss_common.admin import admin_client, check_isolation, ensure_acls, ensure_topics
    from raildss_common.bus import KafkaSettings

    admin = admin_client(KafkaSettings.from_env())
    if args.cmd == "check":
        problems = check_isolation(admin, catalogue)
        for p in problems:
            print(f"FAIL  {p}")
        print("ground-truth isolation (DQ-12):", "FAILED" if problems else "OK")
        return 1 if problems else 0

    topics = ensure_topics(admin, catalogue)
    acls = ensure_acls(admin, catalogue)
    print(
        f"topics: {len(topics.created)} created, {len(topics.updated)} updated, "
        f"{len(topics.unchanged)} unchanged"
    )
    for name in topics.created:
        print(f"  created {name}")
    for name in topics.updated:
        print(f"  updated config of {name}")
    print(f"acls: {acls.created} created, {acls.existing} already present")
    for line in topics.drift:
        print(f"DRIFT {line}")
    for line in acls.extra:
        print(f"EXTRA {line}  (not in the catalogue; review and remove by hand)")
    if topics.drift:
        return 1
    if args.expect_no_changes and (topics.changed or acls.changed):
        print("FAIL  --expect-no-changes: the run changed the broker")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_common")
    sub = ap.add_subparsers(dest="area", required=True)
    p_schema = sub.add_parser("schema", help="envelope JSON Schema")
    p_schema.add_argument("cmd", choices=["generate", "check"])
    p_kafka = sub.add_parser("kafka", help="topics and ACLs")
    p_kafka.add_argument("cmd", choices=["lint", "apply", "check"])
    p_kafka.add_argument("--catalogue", type=Path, default=None, help="default: config/kafka/topics.yaml")
    p_kafka.add_argument("--expect-no-changes", action="store_true", help="apply: fail if anything changed")
    args = ap.parse_args(argv)
    return _schema(args.cmd) if args.area == "schema" else _kafka(args)


if __name__ == "__main__":
    raise SystemExit(main())
