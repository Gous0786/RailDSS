"""Parameter register tests (issue #3 acceptance criteria)."""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pytest
import yaml

from raildss_params import RegisterError, UnknownParameterError, get, load, value
from raildss_params.__main__ import DUMMY_REPORT, dummy_report
from raildss_params.register import lint

ROOT = Path(__file__).resolve().parents[2]
REGISTER = ROOT / "config" / "parameters.yaml"
TRAIN_CSV = ROOT / "data" / "raw" / "ktv-psa" / "KTV_PSA_Passenger_Schedule" / "TRAIN-KTV-PSA-Table 1.csv"


def _doc() -> dict[str, Any]:
    doc: dict[str, Any] = yaml.safe_load(REGISTER.read_text(encoding="utf-8"))
    return doc


def _entry(**overrides: Any) -> dict[str, Any]:
    e: dict[str, Any] = {
        "key": "test.value_m",
        "value": 5,
        "unit": "m",
        "source": "R06 §2",
        "status": "SA",
        "owner": "data",
        "valid_from": "2026-11-03",
    }
    e.update(overrides)
    return e


def _lint_one(entry: dict[str, Any]) -> list[str]:
    return lint({"register_version": "x", "spec_version": "1.3", "parameters": [entry]})


# ---------------------------------------------------------------- the committed register


def test_committed_register_is_clean() -> None:
    assert lint(_doc()) == []


def test_all_appendix_b_keys_load() -> None:
    reg = load()
    assert len(reg.parameters) == len(_doc()["parameters"])
    assert reg.version.startswith(reg.register_version + "+")


def test_psa_csr_returns_central_low_high() -> None:
    p = get("station_line.csr.psa")
    assert p.status == "EST"
    assert p.value == {"loop": 733, "main": 752}
    assert p.bounds is not None
    assert p.bounds.low == {"loop": 673, "main": 678}
    assert p.bounds.high["loop"] == 1150


def test_unknown_key_raises() -> None:
    with pytest.raises(UnknownParameterError):
        get("no.such_key")


def test_value_ref_entries_cannot_be_read_as_values() -> None:
    assert get("station_line.csr").value_ref == "STTNLINE.MANCSR"
    with pytest.raises(RegisterError, match="per record"):
        value("station_line.csr")


def test_spacing_is_dummy_with_provenance() -> None:
    p = get("abs.subblock_spacing_km.ktv_vzm")
    assert (p.value, p.status) == (1.0, "DUMMY")
    assert p.provenance and p.provenance.startswith("https://")


# ---------------------------------------------------------------- lint rules (DQ-13, FR-DATA-08, C-14)


def test_lint_rejects_missing_source() -> None:
    e = _entry()
    del e["source"]
    assert any("missing 'source'" in p for p in _lint_one(e))


def test_lint_rejects_est_without_bounds() -> None:
    assert any("bounds" in p for p in _lint_one(_entry(status="EST")))
    assert _lint_one(_entry(status="EST", bounds={"low": 1, "high": 9})) == []


def test_lint_rejects_dummy_without_provenance() -> None:
    assert any("provenance" in p for p in _lint_one(_entry(status="DUMMY")))
    assert _lint_one(_entry(status="DUMMY", provenance="placeholder")) == []
    assert _lint_one(_entry(status="DUMMY", provenance="https://example.org/x")) == []


def test_lint_rejects_data_without_table_or_report() -> None:
    assert any("DATA source" in p for p in _lint_one(_entry(status="DATA", source="somewhere")))
    assert _lint_one(_entry(status="DATA", source="STTNLINE.MANCSR")) == []


def test_lint_rejects_value_and_ref_together_and_bad_keys() -> None:
    assert any("exactly one" in p for p in _lint_one(_entry(value_ref="STTNLINE.MANCSR")))
    assert any("dotted lower-case" in p for p in _lint_one(_entry(key="BadKey")))
    assert any("unknown field" in p for p in _lint_one(_entry(colour="red")))
    assert any("status must be" in p for p in _lint_one(_entry(status="MAYBE")))


def test_lint_rejects_duplicate_keys() -> None:
    doc = {"register_version": "x", "spec_version": "1.3", "parameters": [_entry(), _entry()]}
    assert any("duplicate key" in p for p in lint(doc))


def test_load_refuses_invalid_register(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        yaml.safe_dump({"register_version": "x", "spec_version": "1", "parameters": [_entry(status="EST")]})
    )
    with pytest.raises(RegisterError, match="EST entries need bounds"):
        load(bad)


# ---------------------------------------------------------------- policy class map vs the dataset


def test_class_map_covers_every_train_type_in_dataset() -> None:
    class_map = value("policy.class_map")
    with TRAIN_CSV.open(encoding="utf-8", errors="replace", newline="") as fh:
        types = {row["TRAIN_TYPE"].strip() for row in csv.DictReader(fh) if row.get("TRAIN_TYPE")}
    assert types, "no TRAIN_TYPE values read from the dataset"
    unmapped = sorted(types - class_map.keys())
    assert unmapped == [], f"TRAIN_TYPE codes without a priority class: {unmapped}"


def test_every_class_has_a_weight() -> None:
    weights = value("policy.weights")
    assert set(value("policy.class_map").values()) <= weights.keys()


# ---------------------------------------------------------------- generated dummy report


def test_dummy_report_is_up_to_date() -> None:
    committed = (ROOT / DUMMY_REPORT).read_text(encoding="utf-8")
    assert committed == dummy_report(load()), "run: python -m raildss_params dummy-report --write"


def test_dummy_report_lists_every_dummy_entry() -> None:
    report = dummy_report(load())
    for p in load().by_status("DUMMY"):
        assert f"`{p.key}`" in report
