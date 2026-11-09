"""``python -m raildss_verifier lint``: the FR-VER-04 catalogue gate (CI)."""

from __future__ import annotations

import argparse
import sys

from raildss_params import load as load_register
from raildss_verifier import catalogue as cat


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="raildss_verifier")
    sub = parser.add_subparsers(dest="cmd", required=True)
    lint = sub.add_parser("lint", help="validate the rule catalogue (sources, tests, predicates, parameters)")
    lint.add_argument("--catalogue", default=None)
    args = parser.parse_args(argv)
    try:
        catalogue = cat.load(args.catalogue)
    except cat.CatalogueError as exc:
        print(f"rule catalogue invalid:\n{exc}", file=sys.stderr)
        return 1
    problems = cat.lint(catalogue, load_register())
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"rule catalogue {catalogue.catalogue_version}: {len(catalogue.rules)} rules OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
