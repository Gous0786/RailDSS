"""ETL stage 3: station lines, platforms and the PSA fix (spec FR-TWIN-03, DQ-16/17/18, REC-23; issue #8).

* ``StationLine`` from STTNLINE: CSR, length, kind, line speed, platform flag, electrification.
* Direction: STTNLINE's value is cross-checked against Connections (the directions of the block
  sections a line receives from and sends to). Connections wins on conflict; every conflict is
  reported (DQ-18).
* PSA fix (R06 §4.2, App. C): number and name columns are swapped; CSR and length are placeholders,
  so CSR comes from the derived table with ``csr_status = estimated`` and low/high bounds.
* ``Platform`` from the Platform table, joined to station lines on station + line sequence.
* Halt main lines (no loops) carry no CSR.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import pandas as pd
from pydantic import BaseModel

from raildss_etl.mapping import REPO, MappingSpec, load_mapping
from raildss_etl.topology import DEFAULT_OUT, Codec, raw_fields
from raildss_params import Register, load
from raildss_schema.entities import Platform, StationLine


class Stage3Report(BaseModel):
    station_lines: int
    platforms: int
    halt_lines: int
    estimated_csr_lines: list[str]
    lines_missing_csr: list[str]
    direction_conflicts: list[str]
    platforms_unjoined: list[str]
    lines_without_connections: list[str]
    loop_csr_median_m: float
    loop_csr_n: int
    main_csr_median_m: float
    main_csr_n: int
    notes: list[str]

    @property
    def ok(self) -> bool:
        return not self.lines_missing_csr and not self.platforms_unjoined


def _connection_directions(tables: dict[str, pd.DataFrame], codec: Codec) -> dict[tuple[str, str], str]:
    """Station line -> DN / UP / BOTH from the directions of the blocks it receives from / sends to."""
    block_dir = {str(r["MAVBLCKSCTN"]): codec.text(r["MAVDRTN"]) for _, r in tables["BLOCKSCTN"].iterrows()}
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for _, c in tables["CONNECTIONS"].iterrows():
        d = block_dir.get(str(c["MAVBLCKSCTN"]))
        if d:
            seen[(str(c["MAVSTTNCODE"]), str(c["MANSTTNLINENUMB"]))].add(d)
    both = codec.decode("line_direction", "MID")
    return {k: (both if len(v) > 1 else next(iter(v))) for k, v in seen.items()}


def build_station_lines(
    tables: dict[str, pd.DataFrame],
    mapping: MappingSpec | None = None,
    register: Register | None = None,
) -> tuple[list[StationLine], list[Platform], Stage3Report]:
    spec = mapping or load_mapping()
    reg = register or load()
    codec = Codec(spec)
    nv = str(reg.value("network.version"))
    swapped = set(spec.fixes.get("swapped_line_number_name", []))
    halts = {
        str(s["MAVSTTNCODE"])
        for _, s in tables["STATION"].iterrows()
        if codec.decode("yes_no", s["MACHALTFLAG"])
    }
    derived = pd.read_csv(REPO / spec.derived_files["psa_station_lines"], dtype=str, keep_default_na=False)
    est = {(str(r["station"]), str(r["line_no"])): r for _, r in derived.iterrows()}
    conn_dir = _connection_directions(tables, codec)
    sl_spec = spec.tables["STTNLINE"]

    lines: list[StationLine] = []
    conflicts: list[str] = []
    missing: list[str] = []
    estimated: list[str] = []
    no_conn: list[str] = []
    for _, r in tables["STTNLINE"].iterrows():
        op, seq = str(r["MAVSTTNCODE"]), str(r["MANSEQNUMB"])
        key = f"{op}/{seq}"
        name = codec.text(r["MAVLINENUMB"] if op in swapped else r["MAVLINENAME"])
        csr = codec.number(r["MANCSR"])
        length = codec.number(r["MANLINELENGTH"])
        csr_status: Literal["data", "estimated"] = "data"
        low: float | None = None
        high: float | None = None
        if op in swapped:
            e = est[(op, seq)]
            csr, length = Decimal(str(e["csr_estimate_m"])), None
            low, high = float(e["csr_low_m"]), float(e["csr_high_m"])
            csr_status = "estimated"
            estimated.append(key)
        elif op in halts:
            csr = None
        if (csr is None and op not in halts) or (csr is not None and csr <= 0):
            missing.append(key)
        sttn_dir = codec.decode("line_direction", r["MAVDRTN"])
        direction: Any = conn_dir.get((op, seq))  # validated by StationLine
        if direction is None:
            direction = sttn_dir
            no_conn.append(key)
        elif direction != sttn_dir:
            conflicts.append(f"{key} ({name}): STTNLINE {sttn_dir} -> Connections {direction}")
        speed = codec.number(r["MANLINESPEED"])
        lines.append(
            StationLine(
                network_version=nv,
                op_code=op,
                line_no=int(seq),
                name=name,
                kind=codec.decode("station_line_kind", r["MACLINECATEGORY"]),
                csr_m=None if csr is None else float(csr),
                csr_status=csr_status,
                csr_low_m=low,
                csr_high_m=high,
                length_m=None if length is None else float(length),
                line_speed_kmh=None if not speed else float(speed),
                directions=direction,
                capacity_trains=int(r["MANNUMBTRAINALLWD"]),
                platform=bool(codec.decode("yes_no", r["MACPLATFORM"])),
                electrified=codec.decode("traction_electric", r["MACTRTNTYPE"]),
                raw=raw_fields(r, sl_spec),
            )
        )
    lines.sort(key=lambda x: (x.op_code, x.line_no))
    line_keys = {(x.op_code, str(x.line_no)) for x in lines}

    pf_spec = spec.tables["PLATFORM"]
    platforms: list[Platform] = []
    unjoined: list[str] = []
    for _, p in tables["PLATFORM"].iterrows():
        op, seq = str(p["MAVSTTNCODE"]), str(p["MANSEQNUMB"])
        if (op, seq) not in line_keys:
            unjoined.append(f"{op}/{seq} {p['MAVPLATFORMNUMB']}")
            continue
        length = codec.number(p["MANLENGTH"])
        platforms.append(
            Platform(
                network_version=nv,
                op_code=op,
                platform_no=str(p["MAVPLATFORMNUMB"]),
                station_line_no=int(seq),
                length_m=float(length) if length else None,
                platform_type=codec.decode("platform_type", p["MAVPFTYPE"]),
                side=codec.decode("platform_side", p["MAVPFVPOSITION"]),
                raw=raw_fields(p, pf_spec),
            )
        )
    platforms.sort(key=lambda x: (x.op_code, x.platform_no))

    def csr_of(kind: str) -> list[float]:
        return [x.csr_m for x in lines if x.kind == kind and x.csr_status == "data" and x.csr_m]

    loops, mains = csr_of("loop"), csr_of("main")
    pdu_numbers = sorted(
        str(r["MAVLINENUMB"]) for _, r in tables["STTNLINE"].iterrows() if str(r["MAVSTTNCODE"]) == "PDU"
    )
    report = Stage3Report(
        station_lines=len(lines),
        platforms=len(platforms),
        halt_lines=sum(1 for x in lines if x.op_code in halts),
        estimated_csr_lines=estimated,
        lines_missing_csr=missing,
        direction_conflicts=conflicts,
        platforms_unjoined=unjoined,
        lines_without_connections=no_conn,
        loop_csr_median_m=statistics.median(loops),
        loop_csr_n=len(loops),
        main_csr_median_m=statistics.median(mains),
        main_csr_n=len(mains),
        notes=[
            "R06 §4.2 reports 'PDU line 5 missing from STTNLINE'. In this extract PDU has line sequences "
            f"1-5 (all connected); its MAVLINENUMB values are {pdu_numbers}, i.e. no line *number* 5.",
        ],
    )
    return lines, platforms, report


def write_station_lines(
    lines: list[StationLine], platforms: list[Platform], report: Stage3Report, out_dir: Path = DEFAULT_OUT
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, items in (("station_lines", lines), ("platforms", platforms)):
        (out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(i.model_dump(mode="json"), sort_keys=True) + "\n" for i in items),
            encoding="utf-8",
        )
    (out_dir / "stations_report.json").write_text(
        json.dumps({**report.model_dump(mode="json"), "ok": report.ok}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
