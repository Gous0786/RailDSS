"""COA-master -> canonical mapping specification (spec §6.6, FR-DATA-07, DQ-22; issue #5).

The mapping lives in ``config/mapping/ktv_psa.yaml``. This module loads and validates it:

* every column of every source file is listed (mapped, ``raw`` or ``ignore``), except columns
  matching an ``ignore_column_patterns`` entry;
* every ``Entity.field`` target exists in :mod:`raildss_schema.entities`;
* ignored columns are never referenced by ETL code (DQ-22);
* a Markdown view is generated for SME review (``docs/etl/mapping-ktv-psa.md``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any, Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from raildss_schema.entities import ENTITIES

REPO = Path(__file__).resolve().parents[2]
DEFAULT_MAPPING = REPO / "config" / "mapping" / "ktv_psa.yaml"
MAPPING_DOC = REPO / "docs" / "etl" / "mapping-ktv-psa.md"
ENTITY_FIELDS: dict[str, set[str]] = {m.__name__: set(m.model_fields) for m in ENTITIES}
SPECIAL_TARGETS = ("raw", "ignore")


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ColumnSpec(_Model):
    to: str | list[str]
    conf: Literal["H", "M", "L", "-"]
    date: str | None = None
    note: str | None = None

    @property
    def targets(self) -> list[str]:
        return self.to if isinstance(self.to, list) else [self.to]

    @property
    def ignored(self) -> bool:
        return self.targets == ["ignore"]


class CleanPolicy(_Model):
    drop_blank_rows: bool = True
    drop_header_rows: bool = True
    deduplicate: Literal["exact", "none"] = "none"


class TableSpec(_Model):
    file: str
    description: str
    entity: str | None
    expected_rows: int = Field(ge=0)
    clean: CleanPolicy
    columns: dict[str, ColumnSpec]


class IgnorePattern(_Model):
    pattern: str
    note: str

    @field_validator("pattern")
    @classmethod
    def _compiles(cls, v: str) -> str:
        re.compile(v)
        return v


class MappingSpec(_Model):
    mapping_version: str
    dataset: str
    raw_dir: str
    timezone: str
    open_ended_years: dict[str, int]
    date_formats: dict[str, str]
    schedule_seconds_columns: list[str]
    ignore_column_patterns: list[IgnorePattern]
    null_tokens: list[str] = Field(default_factory=list)
    value_maps: dict[str, dict[str, Any]] = Field(default_factory=dict)
    fixes: dict[str, list[str]] = Field(default_factory=dict)
    derived_files: dict[str, str] = Field(default_factory=dict)
    wtt: dict[str, Any] = Field(default_factory=dict)
    tables: dict[str, TableSpec]

    def is_pattern_ignored(self, column: str) -> bool:
        return any(re.match(p.pattern, column) for p in self.ignore_column_patterns)

    def ignored_columns(self) -> set[str]:
        return {c for t in self.tables.values() for c, s in t.columns.items() if s.ignored}

    def date_columns(self, table: str) -> dict[str, str]:
        """Column -> strptime format for one table."""
        return {
            c: self.date_formats[s.date] for c, s in self.tables[table].columns.items() if s.date is not None
        }


def load_mapping(path: Path | None = None) -> MappingSpec:
    p = path or DEFAULT_MAPPING
    return MappingSpec.model_validate(yaml.safe_load(p.read_text(encoding="utf-8")))


def source_header(raw_dir: Path, table: TableSpec) -> list[str]:
    """Column names exactly as the loader sees them (pandas names empty headers 'Unnamed: N')."""
    return [str(c) for c in pd.read_csv(raw_dir / table.file, dtype=str, nrows=0, encoding="utf-8").columns]


def check(mapping: MappingSpec, raw_dir: Path | None = None) -> list[str]:
    """All mapping problems against the dataset headers and the canonical schema."""
    raw = raw_dir or (REPO / mapping.raw_dir)
    problems: list[str] = []
    for name, table in mapping.tables.items():
        if table.entity is not None and table.entity not in ENTITY_FIELDS:
            problems.append(f"{name}: unknown entity {table.entity!r}")
        path = raw / table.file
        if not path.exists():
            problems.append(f"{name}: file not found: {table.file}")
            continue
        header = source_header(raw, table)
        listed = set(table.columns)
        for col in header:
            if col not in listed and not mapping.is_pattern_ignored(col):
                problems.append(f"{name}: column {col!r} is neither mapped nor ignored")
        for col in sorted(listed - set(header)):
            problems.append(f"{name}: mapped column {col!r} is not in {table.file}")
        for col, spec in table.columns.items():
            for target in spec.targets:
                if target in SPECIAL_TARGETS:
                    continue
                entity, _, field = target.partition(".")
                if entity not in ENTITY_FIELDS:
                    problems.append(f"{name}.{col}: unknown entity in target {target!r}")
                elif field not in ENTITY_FIELDS[entity]:
                    problems.append(f"{name}.{col}: {entity} has no field {field!r}")
            if spec.ignored and not spec.note:
                problems.append(f"{name}.{col}: ignored columns need a note with the reason")
            if "ignore" in spec.targets and len(spec.targets) > 1:
                problems.append(f"{name}.{col}: 'ignore' cannot be combined with other targets")
            if spec.date is not None and spec.date not in mapping.date_formats:
                problems.append(f"{name}.{col}: unknown date format {spec.date!r}")
    return problems


def ignored_references(mapping: MappingSpec, code_root: Path | None = None) -> list[str]:
    """DQ-22: ETL code must not reference ignored source columns (string literals)."""
    root = code_root or Path(__file__).resolve().parent
    ignored = mapping.ignored_columns()
    problems: list[str] = []
    for py in sorted(root.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in ignored:
                problems.append(f"{py.name}:{node.lineno}: references ignored column {node.value!r}")
    return problems


def render_markdown(mapping: MappingSpec) -> str:
    lines = [
        "# KTV-PSA mapping: COA master tables -> canonical schema",
        "",
        "Generated by `python -m raildss_etl mapping-doc --write` from `config/mapping/ktv_psa.yaml`.",
        "Do not edit by hand. `raw` = kept in the entity's `raw` field (unused); `ignore` = dropped.",
        "",
        f"Mapping version `{mapping.mapping_version}`; timezone of source timestamps `{mapping.timezone}`;",
        f"open-ended years {mapping.open_ended_years}.",
        "",
        "Columns matching these patterns are ignored: "
        + "; ".join(f"`{p.pattern}` ({p.note})" for p in mapping.ignore_column_patterns),
        "",
        f"Null tokens: {', '.join(repr(x) for x in mapping.null_tokens)}.",
        "",
        "## Value maps",
        "",
        "| Map | Code -> value |",
        "|---|---|",
        *[
            f"| `{name}` | " + ", ".join(f"`{k}` -> `{v}`" for k, v in vm.items()) + " |"
            for name, vm in mapping.value_maps.items()
        ],
        "",
    ]
    for name, t in mapping.tables.items():
        lines += [
            f"## {name}",
            "",
            f"`{t.file}` · {t.description} · entity: `{t.entity or '—'}` · expected rows after "
            f"cleaning: {t.expected_rows} · dedupe: {t.clean.deduplicate}",
            "",
            "| Column | Target | Conf. | Date | Note |",
            "|---|---|---|---|---|",
        ]
        for col, s in t.columns.items():
            target = ", ".join(f"`{x}`" for x in s.targets)
            note = (s.note or "").replace("|", "\\|")
            lines.append(f"| `{col}` | {target} | {s.conf} | {s.date or ''} | {note} |")
        lines.append("")
    return "\n".join(lines)
