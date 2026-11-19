"""Network publication tests (issue #18 acceptance criteria).

Most tests read the committed publication (fast). ``test_rebuild_is_byte_identical`` runs the whole
ETL and compares it with the committed files, which is also what CI's ``publish --check`` does.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from raildss_etl import cli, dq, publish, validator
from raildss_params import load
from raildss_params.register import default_path
from raildss_schema.entities import ENTITIES

VERSION = "KTV-PSA-2025-08-r2"
DIR = publish.default_dir(VERSION)


@pytest.fixture(scope="module")
def export() -> tuple[dict[str, Any], dict[str, list[Any]], dict[str, str]]:
    return publish.read_export(DIR)


def _validate(tables: dict[str, list[Any]]) -> validator.ValidationReport:
    return validator.validate(tables, load(), boundary_blocks=("KTV-PDT", "PDT-KTV"))


def _entities(tables: dict[str, list[Any]]) -> dict[str, list[Any]]:
    return {k: list(v) for k, v in tables.items() if k != "network_version"}


def _rule(report: validator.ValidationReport, rule_id: str) -> validator.RuleResult:
    return next(r for r in report.rules if r.id == rule_id)


def test_committed_publication_is_valid_with_zero_errors(export: Any) -> None:
    meta, tables, _ = export
    assert meta["validator_errors"] == 0
    rules = tables["network_version"][0].validator_report["rules"]
    assert [r["id"] for r in rules] == [f"N-0{n}" for n in range(1, 9)]
    assert all(r["status"] == "PASS" and r["error_count"] == 0 for r in rules)


def test_hash_of_the_files_matches_network_json(export: Any) -> None:
    meta, _, texts = export
    assert publish.network_hash(texts) == meta["network_hash"]
    assert len(meta["network_hash"]) == 64


def test_every_record_carries_the_version_stamps(export: Any) -> None:
    meta, tables, _ = export
    assert set(tables) == {m.__table__ for m in ENTITIES}
    for name, rows in tables.items():
        for r in rows:
            assert r.network_version == VERSION, name
            if name in validator.TIMETABLE_TABLES:
                assert r.timetable_version == meta["timetable_version"], name


def test_network_version_record_has_source_hashes_and_reports(export: Any) -> None:
    _, tables, _ = export
    [record] = tables["network_version"]
    assert record.network_version == VERSION
    assert sum(p.startswith("data/raw/ktv-psa/") for p in record.source_hashes) == 14
    assert all(len(h) == 64 for h in record.source_hashes.values())
    assert record.dq_report["network_version"] == VERSION
    assert record.created_at.year == 2026  # fixed from the register, so rebuilds are byte-identical


def test_provenance_card_lists_sources_versions_and_seeds() -> None:
    card = (DIR / publish.CARD_FILE).read_text(encoding="utf-8")
    for text in (
        "Network hash",
        "ETL version",
        "Mapping version",
        "Parameter register",
        "Synthetic components and seeds",
        "data/raw/ktv-psa/KTV-PSA-Infra/STATION-Table 1.csv",
    ):
        assert text in card


def test_counts_per_layer(export: Any) -> None:
    meta, tables, _ = export
    assert meta["counts"]["operational_point"] == 22
    assert meta["counts"]["freight_load"] == len(tables["freight_load"]) > 0
    assert set(meta["layers"]) == set(publish.LAYERS)
    assert sorted(t for names in publish.LAYERS.values() for t in names) == sorted(meta["counts"])


# ---------------------------------------------------------------- the validator blocks bad networks


def test_validator_flags_a_crossing_station_without_a_loop(export: Any) -> None:
    tables = _entities(export[1])
    tables["station_line"] = [
        x for x in tables["station_line"] if not (x.op_code == "KPL" and x.kind == "loop")
    ]
    report = _validate(tables)
    rule = _rule(report, "N-01")
    assert rule.status == "FAIL" and "KPL" in rule.errors[0]
    assert not report.ok


def test_validator_flags_a_block_without_a_length_or_system(export: Any) -> None:
    tables = _entities(export[1])
    bad = tables["block_section"][0].model_copy(update={"length_m": 0.0, "system": ""})
    tables["block_section"] = [bad, *tables["block_section"][1:]]
    rule = _rule(_validate(tables), "N-02")
    assert rule.status == "FAIL" and rule.error_count == 2


def test_validator_flags_a_coa_block_without_gradients_or_a_line_without_a_speed(export: Any) -> None:
    tables = _entities(export[1])
    assert _rule(_validate(tables), "N-08").status == "PASS"
    dropped = tables["gradient"][0]
    tables["gradient"] = [g for g in tables["gradient"] if g.block_section_id != dropped.block_section_id]
    rule = _rule(_validate(tables), "N-08")
    assert rule.status == "FAIL" and dropped.block_section_id in rule.errors[0]
    tables = _entities(export[1])
    slow = tables["block_section_line"][0].model_copy(update={"vmax_kmh": 0.0})
    tables["block_section_line"] = [slow, *tables["block_section_line"][1:]]
    rule = _rule(_validate(tables), "N-08")
    assert rule.status == "FAIL" and "no speed" in rule.errors[0]


def test_validator_flags_a_broken_route(export: Any) -> None:
    tables = _entities(export[1])
    first_dn = next(b for b in tables["block_section"] if b.level == "coa" and b.direction == "DN")
    tables["block_section"] = [b for b in tables["block_section"] if b is not first_dn]
    rule = _rule(_validate(tables), "N-03")
    assert rule.status == "FAIL" and any("chain" in e for e in rule.errors)


def test_validator_flags_a_missing_stamp_and_a_dangling_reference(export: Any) -> None:
    tables = _entities(export[1])
    tables["service"] = [tables["service"][0].model_copy(update={"timetable_version": None})]
    tables["connection_edge"] = [tables["connection_edge"][0].model_copy(update={"op_code": "ZZZ"})]
    report = _validate(tables)
    assert _rule(report, "N-05").status == "FAIL"
    assert _rule(report, "N-04").status == "FAIL"


def test_validator_flags_a_policy_or_catalogue_stamp_on_a_static_table(export: Any) -> None:
    """DR-0007: static tables are exempt from policy_version and rule_catalogue_version (issue #65)."""
    tables = _entities(export[1])
    assert _rule(_validate(tables), "N-05").status == "PASS"
    for stamp in validator.EXEMPT_STAMPS:
        stamped = dict(tables)
        stamped["block_section"] = [stamped["block_section"][0].model_copy(update={stamp: "x"})]
        rule = _rule(_validate(stamped), "N-05")
        assert rule.status == "FAIL", stamp
        assert any(stamp in e and "exempt" in e for e in rule.errors), stamp


def test_published_static_tables_carry_no_policy_or_catalogue_stamp(export: Any) -> None:
    for name, rows in export[1].items():
        for r in rows:
            assert r.policy_version is None and r.rule_catalogue_version is None, name


def test_validator_reports_duplicate_keys(export: Any) -> None:
    tables = _entities(export[1])
    tables["operational_point"] = [*tables["operational_point"], tables["operational_point"][0]]
    assert _rule(_validate(tables), "N-06").status == "FAIL"


def test_publish_is_blocked_when_the_validator_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], export: Any
) -> None:
    tables = _entities(export[1])
    tables["station_line"] = [x for x in tables["station_line"] if x.kind != "loop"]
    report = _validate(tables)

    def blocked() -> None:
        raise publish.PublicationBlockedError(report)

    monkeypatch.setattr(publish, "assemble", blocked)
    assert cli.main(["network", "publish"]) == 1
    out = capsys.readouterr().out
    assert "publication blocked by the validator" in out and "N-01" in out


# ---------------------------------------------------------------- CLI


def test_network_show_prints_counts_per_entity(capsys: pytest.CaptureFixture[str], export: Any) -> None:
    assert cli.main(["network", "show", VERSION]) == 0
    out = capsys.readouterr().out
    assert VERSION in out and "(verified)" in out
    rows = {
        parts[1]: int(parts[2])
        for line in out.splitlines()
        if len(parts := line.split()) == 3 and parts[0] in publish.LAYERS
    }
    for model in ENTITIES:
        assert rows[model.__table__] == len(export[1][model.__table__])


def test_network_show_rejects_an_unknown_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["network", "show", "NOPE-2000"]) == 1
    assert "not published" in capsys.readouterr().out


# ---------------------------------------------------------------- full rebuild (the CI gate)


def test_rebuild_is_byte_identical() -> None:
    files = publish.render(publish.assemble())
    assert publish.diff(files, DIR) == []
    committed = json.loads((DIR / publish.NETWORK_FILE).read_text(encoding="utf-8"))
    assert publish.network_hash(files) == committed["network_hash"]


# ---------------------------------------------------------------- version governance (DR-0006, issue #63)


def test_the_network_hash_covers_content_not_the_metadata_record(export: Any) -> None:
    texts = dict(export[2])
    base = publish.network_hash(texts)
    meta = dict(texts)
    meta["network_version.json"] = texts["network_version.json"].replace(
        '"description":"', '"description":"x', 1
    )
    assert meta != texts and publish.network_hash(meta) == base
    content = dict(texts)
    content["block_section.json"] = texts["block_section.json"].replace('"KTV"', '"KTX"', 1)
    assert content != texts and publish.network_hash(content) != base


def test_a_parameter_edit_that_touches_no_network_record_keeps_the_hash(tmp_path: Path) -> None:
    # PR #50 changed the hash with a parameter edit; the register is metadata (DR-0006 acceptance 1).
    text = default_path().read_text(encoding="utf-8")
    edited = text.replace("      span_tolerance_s: 60\n", "      span_tolerance_s: 61\n", 1)
    assert edited != text
    path = tmp_path / "parameters.yaml"
    path.write_text(edited, encoding="utf-8")
    reg = load(path)
    files = publish.render(publish.assemble(dq.run_stages(register=reg)))
    committed = json.loads((DIR / publish.NETWORK_FILE).read_text(encoding="utf-8"))
    assert json.loads(files[publish.NETWORK_FILE])["register_version"] != committed["register_version"]
    assert publish.network_hash(files) == committed["network_hash"]
    assert publish.changed_content(files, DIR) == []


def test_publish_refuses_a_content_change_under_the_same_label(
    export: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # DR-0006 acceptance 2: a content change needs a new network.version.
    for name in [*export[2], publish.NETWORK_FILE]:
        (tmp_path / name).write_text((DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
    files = dict(export[2])
    assert publish.changed_content(files, tmp_path) == []
    meta_only = dict(files, **{"network_version.json": files["network_version.json"].replace("1", "2", 1)})
    assert publish.changed_content(meta_only, tmp_path) == []
    files["block_section.json"] = files["block_section.json"].replace('"KTV"', '"KTX"', 1)
    assert publish.changed_content(files, tmp_path) == ["block_section.json"]
    assert publish.changed_content(files, tmp_path / "NEW-LABEL") == []  # a new label publishes freely

    stub = SimpleNamespace(network_version=VERSION, validation=SimpleNamespace(rules=[]))
    monkeypatch.setattr(publish, "assemble", lambda: stub)
    monkeypatch.setattr(publish, "render", lambda _a: files)
    before = (tmp_path / "block_section.json").read_text(encoding="utf-8")
    assert cli.main(["network", "publish", "--out", str(tmp_path)]) == 1
    assert "needs a new network.version" in capsys.readouterr().out
    assert (tmp_path / "block_section.json").read_text(encoding="utf-8") == before  # nothing written
