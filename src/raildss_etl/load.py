"""ETL stage 1: load and clean the KTV-PSA CSV files (spec FR-TWIN-01, DQ-14, DQ-20, C-13; issue #6).

Driven by the mapping spec (``config/mapping/ktv_psa.yaml``):

1. load every file with all columns as strings;
2. drop spreadsheet-export columns (``Unnamed: N``), blank rows and repeated header rows;
3. drop exact duplicate rows where the table's policy says so (BLOCKSCTN, BLOCKSCTNLINE, freight);
4. parse date columns per table (``M/D/YY``, ``D-Mon-YY``, ``D/M/YYYY H:M``); year 99 -> 2099;
   freight timestamps are localised to IST;
5. split schedule seconds-since-day-1 into ``<COL>__day_offset`` and ``<COL>__time_s``;
6. freight checks: per-load ordering, negative dwell flagged in ``__negative_dwell``.

Outputs Parquet files and ``stage1_report.json`` under ``data/interim/ktv-psa/`` (git-ignored).
The raw folder is read-only: writing inside it is refused (C-13).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

from raildss_etl.mapping import REPO, MappingSpec, TableSpec, load_mapping

SECONDS_PER_DAY = 86_400
DEFAULT_OUT = REPO / "data" / "interim" / "ktv-psa"
FREIGHT = "FREIGHT"
NEGATIVE_DWELL = "__negative_dwell"


class TableReport(BaseModel):
    name: str
    file: str
    rows_raw: int
    blank_rows: int
    header_rows: int
    duplicate_rows: int
    rows_clean: int
    expected_rows: int
    unnamed_columns_dropped: int
    unnamed_nonempty_cells: int
    date_parse_errors: dict[str, int] = Field(default_factory=dict)
    open_ended_dates: int = 0

    @property
    def ok(self) -> bool:
        return self.rows_clean == self.expected_rows and not any(self.date_parse_errors.values())


class FreightChecks(BaseModel):
    loads: int
    loads_out_of_order: int
    negative_dwell_rows: int
    missing_departure_rows: int


class Stage1Report(BaseModel):
    mapping_version: str
    tables: dict[str, TableReport]
    freight: FreightChecks | None = None

    @property
    def ok(self) -> bool:
        return all(t.ok for t in self.tables.values())


def _tz(offset: str) -> dt.timezone:
    m = re.fullmatch(r"(?P<sign>[+-])(?P<hours>\d{2}):(?P<minutes>\d{2})", offset)
    if not m:
        raise ValueError(f"timezone must look like +05:30, got {offset!r}")
    sign = -1 if m["sign"] == "-" else 1
    return dt.timezone(sign * dt.timedelta(hours=int(m["hours"]), minutes=int(m["minutes"])))


def read_raw(raw_dir: Path, table: TableSpec) -> pd.DataFrame:
    return pd.read_csv(raw_dir / table.file, dtype=str, keep_default_na=False, encoding="utf-8")


def _parse_dates(
    series: pd.Series, fmt: str, open_ended: dict[int, int], tz: dt.timezone | None
) -> tuple[pd.Series, int, int]:
    """Return (parsed, parse_errors, open_ended_count). Blank strings become NaT, not errors."""
    text = series.str.strip()
    parsed = pd.to_datetime(text.where(text != ""), format=fmt, errors="coerce")
    errors = int(((text != "") & parsed.isna()).sum())
    opened = 0
    for year, replacement in open_ended.items():
        hit = parsed.dt.year == year
        opened += int(hit.sum())
        parsed = parsed.where(~hit, parsed + pd.offsets.DateOffset(years=replacement - year))
    if tz is not None:
        parsed = parsed.dt.tz_localize(tz)
    return parsed, errors, opened


def clean_table(name: str, raw: pd.DataFrame, mapping: MappingSpec) -> tuple[pd.DataFrame, TableReport]:
    table = mapping.tables[name]
    unnamed = [c for c in raw.columns if mapping.is_pattern_ignored(c)]
    unnamed_nonempty = (
        int((raw[unnamed].apply(lambda s: s.str.strip() != "")).to_numpy().sum()) if unnamed else 0
    )
    df = raw.drop(columns=unnamed)
    cols = list(df.columns)

    blank_mask = (df.apply(lambda s: s.str.strip() == "")).all(axis=1)
    header_mask = pd.Series(True, index=df.index)
    for c in cols:
        header_mask &= df[c].str.strip() == c
    if not table.clean.drop_blank_rows:
        blank_mask[:] = False
    if not table.clean.drop_header_rows:
        header_mask[:] = False
    df = df[~blank_mask & ~header_mask]
    dup_mask = (
        df.duplicated(keep="first")
        if table.clean.deduplicate == "exact"
        else pd.Series(False, index=df.index)
    )
    df = df[~dup_mask].reset_index(drop=True)

    open_ended = {int(k): v for k, v in mapping.open_ended_years.items()}
    tz = _tz(mapping.timezone)
    errors: dict[str, int] = {}
    opened = 0
    for col, fmt in mapping.date_columns(name).items():
        has_time = "%H" in fmt
        parsed, n_err, n_open = _parse_dates(df[col], fmt, open_ended, tz if has_time else None)
        df[col] = parsed if has_time else parsed.dt.date
        errors[col] = n_err
        opened += n_open

    report = TableReport(
        name=name,
        file=table.file,
        rows_raw=len(raw),
        blank_rows=int(blank_mask.sum()),
        header_rows=int(header_mask.sum()),
        duplicate_rows=int(dup_mask.sum()),
        rows_clean=len(df),
        expected_rows=table.expected_rows,
        unnamed_columns_dropped=len(unnamed),
        unnamed_nonempty_cells=unnamed_nonempty,
        date_parse_errors=errors,
        open_ended_dates=opened,
    )
    return df, report


def split_schedule_seconds(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Add ``<COL>__day_offset`` and ``<COL>__time_s`` for seconds-since-day-1 columns."""
    out = df.copy()
    for col in columns:
        secs = pd.to_numeric(out[col].str.strip().replace("", None), errors="raise").astype("Int64")
        out[col] = secs
        out[f"{col}__day_offset"] = secs // SECONDS_PER_DAY
        out[f"{col}__time_s"] = secs % SECONDS_PER_DAY
    return out


def freight_checks(
    df: pd.DataFrame, load_col: str, arr_col: str, dep_col: str
) -> tuple[pd.DataFrame, FreightChecks]:
    """Flag negative dwell; count loads whose arrival times decrease in file order."""
    out = df.copy()
    out[NEGATIVE_DWELL] = (out[dep_col] < out[arr_col]).fillna(False).astype(bool)
    backwards = out.groupby(load_col, sort=False)[arr_col].diff() < pd.Timedelta(0)
    out_of_order = int(out.loc[backwards.fillna(False), load_col].nunique())
    checks = FreightChecks(
        loads=int(out[load_col].nunique()),
        loads_out_of_order=out_of_order,
        negative_dwell_rows=int(out[NEGATIVE_DWELL].sum()),
        missing_departure_rows=int(out[dep_col].isna().sum()),
    )
    return out, checks


def _freight_columns(mapping: MappingSpec) -> tuple[str, str, str]:
    """Load-id, arrival and departure columns of the freight table, read from the mapping."""
    cols = mapping.tables[FREIGHT].columns
    load_col = next(c for c, s in cols.items() if "FreightLoad.load_id" in s.targets)
    arr_col, dep_col = (c for c, s in cols.items() if s.date is not None)  # mapping order: arrival, departure
    return load_col, arr_col, dep_col


def run_stage1(
    mapping: MappingSpec | None = None,
    raw_dir: Path | None = None,
    out_dir: Path | None = None,
    write: bool = True,
) -> tuple[dict[str, pd.DataFrame], Stage1Report]:
    spec = mapping or load_mapping()
    raw = (raw_dir or (REPO / spec.raw_dir)).resolve()
    out = (out_dir or DEFAULT_OUT).resolve()
    if write and out.is_relative_to(raw):
        raise ValueError(f"refusing to write inside the raw data folder {raw} (spec C-13)")

    tables: dict[str, pd.DataFrame] = {}
    reports: dict[str, TableReport] = {}
    for name, table in spec.tables.items():
        df, rep = clean_table(name, read_raw(raw, table), spec)
        if name == "SCHEDULE":
            df = split_schedule_seconds(df, spec.schedule_seconds_columns)
        tables[name], reports[name] = df, rep

    freight = None
    if FREIGHT in tables:
        tables[FREIGHT], freight = freight_checks(tables[FREIGHT], *_freight_columns(spec))

    report = Stage1Report(mapping_version=spec.mapping_version, tables=reports, freight=freight)
    if write:
        out.mkdir(parents=True, exist_ok=True)
        for name, df in tables.items():
            df.to_parquet(out / f"{name.lower()}.parquet", index=False)
        (out / "stage1_report.json").write_text(
            json.dumps(report.model_dump(mode="json"), indent=1, sort_keys=True) + "\n", encoding="utf-8"
        )
    return tables, report
