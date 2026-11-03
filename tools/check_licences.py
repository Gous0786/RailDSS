"""Licence gate for installed dependencies (spec NFR-LIC-02, C-07).

Reads the JSON output of ``pip-licenses --format=json`` and fails when any package is
under a forbidden licence:

* Business Source License (BSL / BUSL)
* Elastic License (ELv2)
* Server Side Public License (SSPL)
* Prosperity Public License
* GNU GPL / AGPL (copyleft that would propagate to RailDSS when linked)

LGPL is not forbidden but needs a licence review (spec NFR-LIC-03), so it is reported
as a warning. Packages with an unknown licence are also reported as warnings.

Usage::

    pip-licenses --format=json --with-system > licences.json
    python tools/check_licences.py licences.json [--allow name ...]

Exit code 0 = pass, 1 = forbidden licence found, 2 = usage / input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN: list[tuple[str, re.Pattern[str]]] = [
    ("BSL", re.compile(r"\bBUSL\b|\bBSL[- ]?1\.1\b|Business Source License", re.I)),
    ("ELv2", re.compile(r"\bELv2\b|Elastic License", re.I)),
    ("SSPL", re.compile(r"\bSSPL\b|Server Side Public License", re.I)),
    ("Prosperity", re.compile(r"Prosperity", re.I)),
    ("AGPL", re.compile(r"\bAGPL|Affero General Public License", re.I)),
    # GPL but not LGPL: a "GPL" token not preceded by "L", or the long form without "Lesser"/"Library".
    ("GPL", re.compile(r"(?<![L\w])GPL|(?<!Lesser )(?<!Library )GNU General Public License", re.I)),
]
REVIEW: list[tuple[str, re.Pattern[str]]] = [
    ("LGPL", re.compile(r"\bLGPL|Lesser General Public License|Library General Public License", re.I)),
]
UNKNOWN = re.compile(r"^\s*(UNKNOWN|)\s*$", re.I)


@dataclass(frozen=True)
class Finding:
    package: str
    version: str
    licence: str
    rule: str


def classify(licence: str) -> tuple[str, str]:
    """Return (status, rule) where status is 'forbidden', 'review', 'unknown' or 'ok'."""
    if UNKNOWN.match(licence):
        return "unknown", "UNKNOWN"
    for rule, pattern in REVIEW:
        if pattern.search(licence):
            # A dual licence that also offers a forbidden option is still only "review":
            # the permissive/LGPL option can be chosen.
            return "review", rule
    for rule, pattern in FORBIDDEN:
        if pattern.search(licence):
            return "forbidden", rule
    return "ok", ""


def check(entries: list[dict[str, str]], allow: set[str]) -> tuple[list[Finding], list[Finding]]:
    forbidden: list[Finding] = []
    warnings: list[Finding] = []
    for e in entries:
        name = e.get("Name", "")
        if name.lower() in allow:
            continue
        licence = e.get("License", "") or ""
        status, rule = classify(licence)
        f = Finding(name, e.get("Version", ""), licence, rule)
        if status == "forbidden":
            forbidden.append(f)
        elif status in ("review", "unknown"):
            warnings.append(f)
    return forbidden, warnings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("licences_json", type=Path, help="pip-licenses --format=json output")
    ap.add_argument("--allow", nargs="*", default=[], help="package names exempted after a recorded review")
    args = ap.parse_args(argv)
    try:
        entries = json.loads(args.licences_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {args.licences_json}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(entries, list):
        print("error: expected a JSON list from pip-licenses", file=sys.stderr)
        return 2
    forbidden, warnings = check(entries, {a.lower() for a in args.allow})
    for w in warnings:
        print(f"WARN  {w.package}=={w.version}: '{w.licence}' ({w.rule}) needs review")
    for f in forbidden:
        print(f"FAIL  {f.package}=={f.version}: '{f.licence}' is forbidden ({f.rule}) by NFR-LIC-02")
    print(f"checked {len(entries)} packages: {len(forbidden)} forbidden, {len(warnings)} to review")
    return 1 if forbidden else 0


if __name__ == "__main__":
    raise SystemExit(main())
