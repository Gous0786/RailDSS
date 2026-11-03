"""Tests for the licence gate (issue #1 AC: CI fails on a forbidden licence)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "check_licences", Path(__file__).resolve().parents[2] / "tools" / "check_licences.py"
)
assert _SPEC and _SPEC.loader
check_licences = importlib.util.module_from_spec(_SPEC)
sys.modules["check_licences"] = check_licences  # dataclasses need the module registered
_SPEC.loader.exec_module(check_licences)


@pytest.mark.parametrize(
    ("licence", "status"),
    [
        ("MIT License", "ok"),
        ("Apache Software License", "ok"),
        ("BSD License", "ok"),
        ("GNU Affero General Public License v3", "forbidden"),
        ("AGPL-3.0-only", "forbidden"),
        ("GNU General Public License v3 (GPLv3)", "forbidden"),
        ("GPL-2.0-or-later", "forbidden"),
        ("Business Source License 1.1", "forbidden"),
        ("BUSL-1.1", "forbidden"),
        ("Elastic License 2.0", "forbidden"),
        ("Server Side Public License", "forbidden"),
        ("Prosperity Public License 3.0.0", "forbidden"),
        ("GNU Lesser General Public License v3 (LGPLv3)", "review"),
        ("LGPL-3.0-or-later", "review"),
        ("UNKNOWN", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify(licence: str, status: str) -> None:
    assert check_licences.classify(licence)[0] == status


def _write(tmp_path: Path, entries: list[dict[str, str]]) -> Path:
    p = tmp_path / "licences.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return p


def test_permissive_environment_passes(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"Name": "ruff", "Version": "1", "License": "MIT License"}])
    assert check_licences.main([str(p)]) == 0


def test_forbidden_dependency_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(
        tmp_path,
        [
            {"Name": "ruff", "Version": "1", "License": "MIT License"},
            {"Name": "some-agpl-lib", "Version": "2", "License": "GNU Affero General Public License v3"},
        ],
    )
    assert check_licences.main([str(p)]) == 1
    assert "some-agpl-lib" in capsys.readouterr().out


def test_lgpl_is_warning_not_failure(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _write(tmp_path, [{"Name": "lgpl-lib", "Version": "1", "License": "LGPLv3"}])
    assert check_licences.main([str(p)]) == 0
    assert "WARN" in capsys.readouterr().out


def test_allow_list_exempts_reviewed_package(tmp_path: Path) -> None:
    p = _write(tmp_path, [{"Name": "Reviewed-GPL-Tool", "Version": "1", "License": "GPLv2"}])
    assert check_licences.main([str(p), "--allow", "reviewed-gpl-tool"]) == 0


def test_bad_input_is_usage_error(tmp_path: Path) -> None:
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    assert check_licences.main([str(p)]) == 2
