"""Dataset integrity (issue #2, spec C-13 / FR-DATA-03).

CI fails if any file under data/raw/ktv-psa or data/derived/ktv-psa changes, appears
or disappears without a manifest update, and the raw manifest cannot be silently
rewritten over changed raw files.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("manifest", ROOT / "tools" / "manifest.py")
assert _SPEC and _SPEC.loader
manifest = importlib.util.module_from_spec(_SPEC)
sys.modules["manifest"] = manifest  # dataclasses need the module registered
_SPEC.loader.exec_module(manifest)

RAW = ROOT / "data" / "raw" / "ktv-psa"
EXPECTED_RAW_FILES = 14


def test_committed_manifests_match_files() -> None:
    for d in manifest.DATASET_DIRS:
        assert manifest.diff(d) == [], f"manifest mismatch in {d}"


def test_raw_dataset_is_complete() -> None:
    entries = manifest.read_manifest(RAW)
    assert len(entries) == EXPECTED_RAW_FILES
    assert "KTV-PSA-Infra/WAT_GOODS_TRAIN_AUG25_01082025_27082025.csv" in entries
    assert (RAW / "PROVENANCE.md").exists()


def test_raw_files_keep_crlf_bytes() -> None:
    # .gitattributes marks data/** as -text; a checkout must not convert line endings,
    # otherwise the hashes would differ between operating systems.
    sample = (RAW / "KTV-PSA-Infra" / "STATION-Table 1.csv").read_bytes()
    assert b"\r\n" in sample


@pytest.fixture()
def dataset_copy(tmp_path: Path) -> Path:
    d = tmp_path / "data" / "raw" / "sample"
    d.mkdir(parents=True)
    (d / "a.csv").write_bytes(b"x,y\r\n1,2\r\n")
    (d / "sub").mkdir()
    (d / "sub" / "b.csv").write_bytes(b"z\r\n3\r\n")
    (d / manifest.MANIFEST).write_text(
        "\n".join(f"{h}  {r}" for r, h in manifest.compute(d).items()) + "\n", encoding="utf-8"
    )
    return d


def test_detects_modified_file(dataset_copy: Path) -> None:
    (dataset_copy / "a.csv").write_bytes(b"x,y\r\n1,3\r\n")
    assert any("hash changed" in p for p in manifest.diff(dataset_copy))


def test_detects_added_and_removed_files(dataset_copy: Path) -> None:
    (dataset_copy / "new.csv").write_bytes(b"n\r\n")
    (dataset_copy / "sub" / "b.csv").unlink()
    problems = manifest.diff(dataset_copy)
    assert any("not in manifest" in p for p in problems)
    assert any("listed but missing" in p for p in problems)


def test_refuses_to_rewrite_raw_manifest_over_changes(dataset_copy: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manifest, "ROOT", dataset_copy.parents[2])
    (dataset_copy / "a.csv").write_bytes(b"tampered\r\n")
    with pytest.raises(SystemExit, match="refusing to rewrite"):
        manifest.write(dataset_copy, force=False)
    manifest.write(dataset_copy, force=True)
    assert manifest.diff(dataset_copy) == []
