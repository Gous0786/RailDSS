"""Data-quality report and regression gate (spec FR-TWIN-21, DQ-14..22, R06 §6.8; issue #17).

``build_report`` runs the whole KTV-PSA ETL in memory (stages 1-4, WTT, freight, boundaries) and
collects, in one deterministic document:

* row counts before and after cleaning, per source table;
* fixes applied (blank / repeated-header / duplicate rows, the PSA swap, date mapping, recomputed
  freight km);
* anomalies (PDU line 5, short lines, STTNLINE vs Connections direction conflicts, negative dwell,
  stabled trains);
* undecoded fields that are ignored;
* the DQ-14..22 checks, each PASS or FAIL.

The report has no timestamps, so two runs on the same inputs are byte-identical. A baseline is
committed under ``docs/etl/dq/``; ``dq --check`` compares a fresh report with it and fails on any
difference in a count or finding, and on any check that is not PASS.
"""

from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel, Field

from raildss_etl import boundaries, freight, load, speeds, stations, timetable, topology
from raildss_etl.load import run_stage1
from raildss_etl.mapping import REPO, MappingSpec, ignored_references, load_mapping
from raildss_params import Register
from raildss_params import load as load_register
from raildss_schema.entities import Platform, StationLine

BASELINE_DIR = REPO / "docs" / "etl" / "dq"
BASELINE_JSON = BASELINE_DIR / "ktv-psa-baseline.json"
BASELINE_MD = BASELINE_DIR / "ktv-psa-baseline.md"
DEFAULT_OUT = REPO / "data" / "interim" / "ktv-psa" / "dq"
REPORT_VERSION = "1"
META_FILE = "dq_meta.json"  # what the report was built with, kept out of the diffed content
GRADIENT_REPORT = REPO / "data" / "derived" / "ktv-psa" / "ktv_psa_gradients_report.json"
GRADIENT_KEYS = ("gradient_records", "clipped", "curves")


class Check(BaseModel):
    id: str
    title: str
    status: Literal["PASS", "FAIL"]
    detail: str


class DqReport(BaseModel):
    report_version: str
    dataset: str
    mapping_version: str
    # The register hash changes with every parameter edit, so it is not report content: excluded from the
    # JSON and Markdown (and so from the baseline diff and the published network) and written to dq_meta.json.
    register_version: str = Field(default="", exclude=True)
    network_version: str
    row_counts: dict[str, dict[str, int]]
    entity_counts: dict[str, int]
    fixes: dict[str, Any]
    anomalies: dict[str, Any]
    ignored_fields: dict[str, list[str]]
    stage_status: dict[str, bool]
    checks: list[Check]

    @property
    def ok(self) -> bool:
        return all(c.status == "PASS" for c in self.checks) and all(self.stage_status.values())


def _check(cid: str, title: str, passed: bool, detail: str) -> Check:
    return Check(id=cid, title=title, status="PASS" if passed else "FAIL", detail=detail)


@dataclass(frozen=True)
class Stages:
    """Everything the ETL builds, in memory (shared by the DQ report and the network publication)."""

    spec: MappingSpec
    register: Register
    tables: dict[str, pd.DataFrame]
    s1: load.Stage1Report
    topo: topology.Topology
    s2: topology.Stage2Report
    lines: list[StationLine]
    platforms: list[Platform]
    s3: stations.Stage3Report
    speed_result: speeds.Speeds
    s4: speeds.Stage4Report
    tt: timetable.Timetable
    s5: timetable.Stage5Report
    demand: freight.FreightDemand
    s6: freight.Stage6Report
    s7: boundaries.BoundaryReport


def run_stages(mapping: MappingSpec | None = None, register: Register | None = None) -> Stages:
    spec = mapping or load_mapping()
    reg = register or load_register()
    tables, s1 = run_stage1(spec, write=False)
    topo, s2 = topology.build_topology(tables, spec, reg)
    lines, platforms, s3 = stations.build_station_lines(tables, spec, reg)
    speed_result, s4 = speeds.build_speeds(tables, topo, lines, spec, reg)
    tt, s5 = timetable.build_timetable(tables, spec)
    demand, s6 = freight.build_freight(tables, topo, spec)
    _, _, s7 = boundaries.build_boundaries(tt, demand, tables, topo, timetable.ist(spec))
    return Stages(
        spec, reg, tables, s1, topo, s2, lines, platforms, s3, speed_result, s4, tt, s5, demand, s6, s7
    )


def build_report(
    mapping: MappingSpec | None = None,
    register: Register | None = None,
    stages: Stages | None = None,
) -> DqReport:
    st = stages or run_stages(mapping, register)
    spec, reg, tables = st.spec, st.register, st.tables
    s1, topo, s2, lines, platforms, s3 = st.s1, st.topo, st.s2, st.lines, st.platforms, st.s3
    speed_result, s4, s5, s6, s7 = st.speed_result, st.s4, st.s5, st.s6, st.s7
    codec = topology.Codec(spec)

    row_counts = {
        name: {
            "raw": t.rows_raw,
            "blank": t.blank_rows,
            "repeated_header": t.header_rows,
            "duplicate": t.duplicate_rows,
            "clean": t.rows_clean,
            "expected": t.expected_rows,
        }
        for name, t in sorted(s1.tables.items())
    }
    loop_p10 = float(reg.value("station_line.csr.stats")["loops"]["p10"])
    short = sorted(
        f"{x.op_code}/{x.line_no} ({x.kind}, CSR {x.csr_m:g} m)"
        for x in lines
        if x.csr_status == "data" and x.csr_m is not None and x.csr_m < loop_p10
    )
    swapped = sorted(spec.fixes.get("swapped_line_number_name", []))
    psa_lines = [x for x in lines if x.op_code in swapped]
    conn_dir = stations._connection_directions(tables, codec)
    off_connections = [
        f"{x.op_code}/{x.line_no}"
        for x in lines
        if (x.op_code, str(x.line_no)) in conn_dir and x.directions != conn_dir[(x.op_code, str(x.line_no))]
    ]
    date_errors = {n: sum(t.date_parse_errors.values()) for n, t in s1.tables.items()}
    fr = s1.freight
    assert fr is not None
    agreement_min = float(reg.value("dq.freight_block_hrs_min_agreement"))
    ref_problems = ignored_references(spec)
    gradients: dict[str, int] = {}
    if GRADIENT_REPORT.exists():
        g = json.loads(GRADIENT_REPORT.read_text(encoding="utf-8"))
        gradients = {f"gradients_{k}": int(g[k]) for k in GRADIENT_KEYS}

    checks = [
        _check(
            "DQ-14",
            "Expected row counts after cleaning; blank, repeated-header and duplicate rows removed",
            all(t.rows_clean == t.expected_rows for t in s1.tables.values()),
            ", ".join(f"{n} {t.rows_clean}/{t.expected_rows}" for n, t in sorted(s1.tables.items())),
        ),
        _check(
            "DQ-15",
            "Sum of block distances equals the cumulative route km; forward vs reverse within tolerance",
            s2.route_sum_km == s2.route_cum_km and s2.reverse_diff_km <= s2.reverse_tolerance_km,
            f"sum {s2.route_sum_km} km, cumulative {s2.route_cum_km} km, reverse {s2.reverse_sum_km} km "
            f"(diff {s2.reverse_diff_km:g}, tolerance {s2.reverse_tolerance_km:g})",
        ),
        _check(
            "DQ-16",
            "PSA line-ID swap applied; PSA directions from Connections; PSA CSR flagged estimated",
            bool(psa_lines)
            and all(x.csr_status == "estimated" for x in psa_lines)
            and len(s3.estimated_csr_lines) == len(psa_lines)
            and not any(c.split("/")[0] in swapped for c in off_connections),
            f"{len(psa_lines)} PSA lines, all estimated; swap applied to {', '.join(swapped)}",
        ),
        _check(
            "DQ-17",
            "Every non-halt running line has CSR > 0",
            not s3.lines_missing_csr,
            f"{len(s3.lines_missing_csr)} lines missing CSR; "
            f"{len(s3.estimated_csr_lines)} CSR values estimated",
        ),
        _check(
            "DQ-18",
            "STTNLINE direction agrees with Connections; otherwise Connections wins and a finding is logged",
            not off_connections,
            f"{len(s3.direction_conflicts)} conflicts logged, Connections applied to all",
        ),
        _check(
            "DQ-19",
            "Schedule: monotonic times, NEXTARVL, DAY_OF_RUN and BLOCK_BUSY_DAYS rules",
            s5.ok,
            f"{s5.services} services, {s5.timetable_points} points; non-monotonic {len(s5.non_monotonic)}, "
            f"NEXTARVL {s5.nextarvl_mismatches}, DAY_OF_RUN {s5.day_of_run_mismatches}, "
            f"BLOCK_BUSY_DAYS {s5.busy_days_mismatches}",
        ),
        _check(
            "DQ-20",
            "Date formats parsed per table; year 99 becomes 2099",
            not any(date_errors.values()),
            f"{sum(date_errors.values())} parse errors; "
            f"{sum(t.open_ended_dates for t in s1.tables.values())} "
            "open-ended dates mapped to 2099",
        ),
        _check(
            "DQ-21",
            "Freight: timestamps increase per load; Block Km and Speed recomputed; Block Hrs agree; "
            "negative dwell and stabled trains flagged",
            fr.loads_out_of_order == 0 and s6.block_hrs_agreement >= agreement_min,
            f"{fr.loads} loads, {fr.loads_out_of_order} out of order; Block Hrs agreement "
            f"{s6.block_hrs_agreement:.4f} over {s6.block_hrs_checked_rows} rows "
            f"(minimum {agreement_min:g}); "
            f"{fr.negative_dwell_rows} negative-dwell rows and {s6.stabled_loads} stabled loads flagged",
        ),
        _check(
            "DQ-22",
            "Undecoded fields are not referenced by ETL code",
            not ref_problems,
            f"{len(spec.ignored_columns())} ignored columns; {len(ref_problems)} references",
        ),
    ]
    ignored = {
        t: sorted(c for c, s in tab.columns.items() if s.ignored)
        for t, tab in sorted(spec.tables.items())
        if any(s.ignored for s in tab.columns.values())
    }
    return DqReport(
        report_version=REPORT_VERSION,
        dataset=spec.dataset,
        mapping_version=spec.mapping_version,
        register_version=reg.version,
        network_version=topo.network_version,
        row_counts=row_counts,
        entity_counts={
            "operational_points": len(topo.operational_points),
            "block_sections": len(topo.block_sections),
            "block_section_lines": len(topo.block_section_lines),
            "connection_edges": len(topo.connection_edges),
            "station_lines": len(lines),
            "platforms": len(platforms),
            "speed_profiles_psr": len(speed_result.speed_profiles),
            "services": s5.services,
            "timetable_points": s5.timetable_points,
            "freight_loads": s6.loads,
            "freight_days": s6.days,
            "wtt_trains_assigned": s7.wtt_trains,
            "freight_loads_assigned": s7.freight_loads,
            **gradients,
        },
        fixes={
            "dropped_rows": {
                n: {"blank": t.blank_rows, "repeated_header": t.header_rows, "duplicate": t.duplicate_rows}
                for n, t in sorted(s1.tables.items())
                if t.blank_rows or t.header_rows or t.duplicate_rows
            },
            "unnamed_columns_dropped": {
                n: t.unnamed_columns_dropped
                for n, t in sorted(s1.tables.items())
                if t.unnamed_columns_dropped
            },
            "psa_swap": {"ops": swapped, "lines": len(psa_lines), "estimated_csr": s3.estimated_csr_lines},
            "date_mapping": {
                n: {"errors": date_errors[n], "open_ended_to_2099": t.open_ended_dates}
                for n, t in sorted(s1.tables.items())
                if t.open_ended_dates or date_errors[n]
            },
            "freight_recomputed": {
                "block_km_and_speed": "dropped and recomputed from the route",
                "block_hrs_agreement": s6.block_hrs_agreement,
                "rows_checked": s6.block_hrs_checked_rows,
            },
            "siding_speed_assigned": s4.assigned_station_lines,
            "empty_topology_rows_dropped": s2.empty_rows_dropped,
        },
        anomalies={
            "pdu_line_5": s3.notes,
            "short_lines": short,
            "direction_conflicts": s3.direction_conflicts,
            "negative_dwell_rows": fr.negative_dwell_rows,
            "stabled_loads": s6.stabled_loads,
            "freight_missing_departure_rows": fr.missing_departure_rows,
            "boundary_blocks": topo.boundary_blocks,
            "wtt_unassigned": s7.wtt_unassigned,
            "freight_unassigned": s7.freight_unassigned,
        },
        ignored_fields=ignored,
        stage_status={
            "stage1": s1.ok,
            "stage2_topology": s2.ok,
            "stage3_station_lines": s3.ok,
            "stage4_speeds": s4.ok,
            "wtt": s5.ok,
            "boundaries": s7.ok,
        },
        checks=checks,
    )


def to_json(report: DqReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def to_markdown(report: DqReport) -> str:
    counts = ("raw", "blank", "repeated_header", "duplicate", "clean", "expected")
    out: list[str] = [
        f"# Data-quality report: {report.dataset}",
        "",
        "Generated by `python -m raildss_etl dq` (spec FR-TWIN-21). Deterministic: no timestamps.",
        "",
        f"Mapping `{report.mapping_version}` · network `{report.network_version}` · "
        f"overall **{'PASS' if report.ok else 'FAIL'}**",
        "",
        "## Checks",
        "",
        *_table(
            ["Check", "Status", "What", "Evidence"],
            [[c.id, c.status, c.title, c.detail] for c in report.checks],
        ),
        "",
        "## Row counts before and after cleaning",
        "",
        *_table(
            ["Table", "Raw", "Blank", "Repeated header", "Duplicate", "Clean", "Expected"],
            [[n, *(r[k] for k in counts)] for n, r in report.row_counts.items()],
        ),
        "",
        "## Entity counts",
        "",
        *_table(["Entity", "Count"], [[k, v] for k, v in report.entity_counts.items()]),
        "",
        "## Fixes applied",
        "",
        "```json",
        json.dumps(report.fixes, indent=1, sort_keys=True, ensure_ascii=False),
        "```",
        "",
        "## Anomalies",
        "",
        "```json",
        json.dumps(report.anomalies, indent=1, sort_keys=True, ensure_ascii=False),
        "```",
        "",
        "## Undecoded fields ignored",
        "",
        *_table(
            ["Table", "Ignored columns"],
            [[t, ", ".join(f"`{c}`" for c in cs)] for t, cs in report.ignored_fields.items()],
        ),
        "",
        "## Stage status",
        "",
        *_table(["Stage", "OK"], [[k, v] for k, v in report.stage_status.items()]),
        "",
    ]
    return "\n".join(out)


def write_report(report: DqReport, out_dir: Path = DEFAULT_OUT) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    as_json, as_markdown, as_meta = out_dir / "dq_report.json", out_dir / "dq_report.md", out_dir / META_FILE
    as_json.write_text(to_json(report), encoding="utf-8", newline="\n")
    as_markdown.write_text(to_markdown(report), encoding="utf-8", newline="\n")
    meta = {"register_version": report.register_version, "report_version": report.report_version}
    as_meta.write_text(json.dumps(meta, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return [as_json, as_markdown, as_meta]


def write_baseline(report: DqReport) -> list[Path]:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)
    BASELINE_JSON.write_text(to_json(report), encoding="utf-8", newline="\n")
    BASELINE_MD.write_text(to_markdown(report), encoding="utf-8", newline="\n")
    return [BASELINE_JSON, BASELINE_MD]


def diff_against_baseline(report: DqReport) -> list[str]:
    """Unified diff lines between the committed baseline and ``report`` (empty when identical)."""
    if not BASELINE_JSON.exists():
        where = BASELINE_JSON.relative_to(REPO) if BASELINE_JSON.is_relative_to(REPO) else BASELINE_JSON
        return [f"no baseline at {where} (run `python -m raildss_etl dq --update-baseline`)"]
    old = BASELINE_JSON.read_text(encoding="utf-8").splitlines()
    new = to_json(report).splitlines()
    return list(difflib.unified_diff(old, new, "baseline", "this run", lineterm="", n=1))
