"""Tests for the literal-value lint (issue #3: no literal rule values in code)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("lint_literals", ROOT / "tools" / "lint_literals.py")
assert _SPEC and _SPEC.loader
lint_literals = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_literals"] = lint_literals
_SPEC.loader.exec_module(lint_literals)


def _module(tmp_path: Path, body: str) -> Path:
    pkg = tmp_path / "raildss_demo"
    pkg.mkdir()
    (pkg / "m.py").write_text(body, encoding="utf-8")
    return pkg


def test_repository_code_passes() -> None:
    assert lint_literals.main([]) == 0


def test_flags_rule_value_literal(tmp_path: Path) -> None:
    pkg = _module(tmp_path, "ADEQUATE_DISTANCE = 180\nflag = True\nx = 0\n")
    problems = lint_literals.scan([pkg], tmp_path / "none.txt")
    assert len(problems) == 1 and "literal 180" in problems[0]


def test_negative_and_float_literals_are_flagged(tmp_path: Path) -> None:
    pkg = _module(tmp_path, "a = -90\nb = 0.7\nc = -1\n")
    found = {p.split("literal ")[1].split()[0] for p in lint_literals.scan([pkg], tmp_path / "none.txt")}
    assert found == {"-90", "0.7"}


def test_allowlist_exempts_reviewed_literal(tmp_path: Path) -> None:
    pkg = _module(tmp_path, "SRID = 4326\n")
    rel = (pkg / "m.py").as_posix()
    allow = tmp_path / "allow.txt"
    allow.write_text(f"{rel}: 4326  # EPSG code\n", encoding="utf-8")
    assert lint_literals.scan([pkg], allow) == []


def test_allowlist_entry_needs_a_reason(tmp_path: Path) -> None:
    allow = tmp_path / "allow.txt"
    allow.write_text("src/x.py: 5\n", encoding="utf-8")
    _, problems = lint_literals.read_allowlist(allow)
    assert problems and "reason" in problems[0]
