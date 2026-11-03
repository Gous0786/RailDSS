"""Lint: no literal rule values in RailDSS code (spec C-05, FR-DATA-01; issue #3).

Rule values (distances, speeds, thresholds, weights...) must come from the parameter
register (``config/parameters.yaml``). This lint scans ``src/raildss_*`` for numeric literals
other than 0, 1 and -1. Each remaining literal must be listed in ``config/literal_allowlist.txt``
with a reason, which is reviewed in the PR that adds it::

    src/raildss_schema/geo.py: 90      # latitude range, not a rule value

Usage: ``python tools/lint_literals.py [--allowlist PATH] [ROOT ...]``. Exit 1 on findings.
"""

from __future__ import annotations

import argparse
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_ALLOWLIST = REPO / "config" / "literal_allowlist.txt"
TRIVIAL = {0, 1, -1}
ENTRY_RE = re.compile(r"^\s*(?P<path>[^#]+?)\s*:\s*(?P<value>-?[0-9.eE+_]+)\s*#\s*(?P<reason>\S.*)$")


def read_allowlist(path: Path) -> tuple[set[tuple[str, float]], list[str]]:
    allowed: set[tuple[str, float]] = set()
    problems: list[str] = []
    if not path.exists():
        return allowed, problems
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = ENTRY_RE.match(line)
        if not m:
            problems.append(f"{path.name}:{n}: expected 'path: value  # reason'")
            continue
        allowed.add((m["path"].replace("\\", "/"), float(m["value"])))
    return allowed, problems


def literals(path: Path) -> list[tuple[int, float]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, float]] = []
    negated: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.UnaryOp)
            and isinstance(node.op, ast.USub)
            and isinstance(node.operand, ast.Constant)
        ):
            negated.add(id(node.operand))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            v = float(node.value) * (-1 if id(node) in negated else 1)
            if v not in TRIVIAL:
                found.append((node.lineno, v))
    return found


def scan(roots: list[Path], allowlist: Path) -> list[str]:
    allowed, problems = read_allowlist(allowlist)
    for root in roots:
        for py in sorted(root.rglob("*.py")):
            rel = (
                py.resolve().relative_to(REPO).as_posix()
                if py.resolve().is_relative_to(REPO)
                else py.as_posix()
            )
            for line, v in literals(py):
                if (rel, v) not in allowed:
                    problems.append(f"{rel}:{line}: literal {v:g} is not in the allow-list")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("roots", nargs="*", type=Path)
    ap.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    args = ap.parse_args(argv)
    roots = args.roots or sorted((REPO / "src").glob("raildss_*"))
    problems = scan(roots, args.allowlist)
    for p in problems:
        print(f"FAIL  {p}")
    print("literal lint:", "FAILED" if problems else "OK")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
