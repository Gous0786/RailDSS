"""Command line for the audit trail.

python -m raildss_audit verify --dir audit --section KTV-PSA
python -m raildss_audit query --dir audit --kind recommendation --train 12503@2025-08-19 --rec REC-0001
python -m raildss_audit export --dir audit --out web/public/data/audit.json   # data of the replay view
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from raildss_audit.chain import AuditLog

NEWLINE = "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_audit")
    ap.add_argument("cmd", choices=["verify", "query", "export"])
    ap.add_argument("--dir", type=Path, required=True)
    ap.add_argument("--section", default="KTV-PSA")
    ap.add_argument("--kind")
    ap.add_argument("--train")
    ap.add_argument("--rec")
    ap.add_argument("--controller")
    ap.add_argument("--incident")
    ap.add_argument("--since")
    ap.add_argument("--until")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args(argv)
    log = AuditLog(args.dir, args.section)
    if args.cmd == "verify":
        result = log.verify()
        print(
            f"{result.records} records on {result.days} days: {'chain intact' if result.ok else 'TAMPERED'}"
        )
        for p in result.problems:
            print(f"  {p}")
        return 0 if result.ok else 1
    rows = log.query(
        kind=args.kind,
        train=args.train,
        controller=args.controller,
        incident=args.incident,
        rec_id=args.rec,
        since=dt.datetime.fromisoformat(args.since) if args.since else None,
        until=dt.datetime.fromisoformat(args.until) if args.until else None,
    )
    if args.cmd == "query":
        for r in rows:
            print(f"{r.time} {r.kind:<14} {r.actor or '-':<18} {json.dumps(r.refs, sort_keys=True)}")
        print(f"{len(rows)} records")
        return 0
    if args.out is None:
        ap.error("export needs --out")
    doc = {
        "section": args.section,
        "verification": log.verify().model_dump(),
        "records": [r.model_dump() for r in rows],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=1) + NEWLINE, encoding="utf-8", newline=NEWLINE)
    print(f"wrote {len(rows)} records to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
