"""Mapping specification tests (issue #5 acceptance criteria)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from raildss_etl.mapping import (
    ENTITY_FIELDS,
    MAPPING_DOC,
    REPO,
    check,
    ignored_references,
    load_mapping,
    render_markdown,
    source_header,
)

RAW = REPO / "data" / "raw" / "ktv-psa"


def test_mapping_covers_all_14_source_files() -> None:
    mapping = load_mapping()
    files = {t.file for t in mapping.tables.values()}
    on_disk = {p.relative_to(RAW).as_posix() for p in RAW.rglob("*.csv")}
    assert files == on_disk
    assert len(files) == 14


def test_every_source_column_is_mapped_raw_or_ignored() -> None:
    mapping = load_mapping()
    for name, table in mapping.tables.items():
        for col in source_header(RAW, table):
            assert col in table.columns or mapping.is_pattern_ignored(col), f"{name}.{col} not listed"


def test_mapping_check_is_clean() -> None:
    assert check(load_mapping()) == []


def test_every_target_field_exists_and_names_its_source() -> None:
    mapping = load_mapping()
    populated: dict[str, set[str]] = {}
    for table in mapping.tables.values():
        for spec in table.columns.values():
            for target in spec.targets:
                if target in ("raw", "ignore"):
                    continue
                entity, _, field = target.partition(".")
                assert field in ENTITY_FIELDS[entity]
                populated.setdefault(entity, set()).add(field)
    # Core dataset-sourced fields named in spec §6.6 must each have a source column.
    for entity, fields in {
        "OperationalPoint": {"code", "name", "km", "crossing_facility", "valid_to"},
        "BlockSection": {"id", "length_m", "system", "level", "direction"},
        "StationLine": {"op_code", "line_no", "csr_m", "kind", "directions"},
        "ConnectionEdge": {"op_code", "station_line_no", "block_section_id", "block_line_id", "mode"},
        "Service": {"train_id", "train_no", "icms_train_type", "days"},
        "TimetablePoint": {"train_id", "seq", "arr_s", "dep_s"},
        "FreightLoad": {"load_id", "wagon_type", "entry_time"},
    }.items():
        assert fields <= populated[entity], f"{entity}: {fields - populated[entity]} have no source column"


def test_ignored_columns_have_reasons() -> None:
    mapping = load_mapping()
    for table in mapping.tables.values():
        for spec in table.columns.values():
            if spec.ignored:
                assert spec.note
    assert {"MANCAPACITY", "MANSTARTBUFFER", "MANENDBUFFER", "Block Km", "Speed"} <= mapping.ignored_columns()


def test_etl_code_does_not_reference_ignored_columns() -> None:
    assert ignored_references(load_mapping()) == []


def test_ignored_reference_lint_catches_a_violation(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text('km = row["Block Km"]\n', encoding="utf-8")
    problems = ignored_references(load_mapping(), tmp_path)
    assert len(problems) == 1 and "Block Km" in problems[0]


def test_mapping_doc_is_up_to_date() -> None:
    assert MAPPING_DOC.read_text(encoding="utf-8") == render_markdown(load_mapping()), (
        "run: python -m raildss_etl mapping-doc --write"
    )


def test_fidelity_notes_are_recorded_and_match_the_published_data() -> None:
    """Issue #69 (DR-0002 F17): each item is confirmed or corrected in the mapping spec, and the
    figures it quotes are the published ones."""
    from raildss_etl import publish

    notes = {n.id: n for n in load_mapping().fidelity_notes}
    assert set(notes) == {"HCM-CROSSING-FLAG", "FREIGHT-SKIPPING-LEGS"}
    assert all(n.status in ("confirmed", "corrected") and n.decision for n in notes.values())
    _, published, _ = publish.read_export(publish.default_dir("KTV-PSA-2025-08"))
    tables: dict[str, Any] = dict(published)
    hcm = next(o for o in tables["operational_point"] if o.code == "HCM")
    assert hcm.is_halt and hcm.crossing_facility
    lines = [x for x in tables["station_line"] if x.op_code == "HCM"]
    assert lines
    assert not [x for x in lines if x.kind == "loop"]
    blocks = {b.id for b in tables["block_section"]}
    legs = [b for ld in tables["freight_load"] for b in ld.path if b not in blocks]
    assert len(legs) == 1029
    assert "1,029" in notes["FREIGHT-SKIPPING-LEGS"].finding
    on_loads = sum(1 for ld in tables["freight_load"] if any(b not in blocks for b in ld.path))
    assert on_loads == 731
