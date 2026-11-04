"""ETL stage 2: topology build (spec FR-TWIN-02, DQ-15, REC-26; issue #7).

From the stage-1 tables this builds the canonical topology entities:

* ``OperationalPoint`` for the 22 section stations (STATION + route table);
* ``BlockSection`` at COA level and at route level split at halts (BLOCKSCTN);
* ``BlockSectionLine`` (DN / MID / UP) for the section's COA block sections (BLOCKSCTNLINE);
* ``ConnectionEdge`` from Connections, with the block line resolved to its line id.

Halts (BTVA, HCM, DGB, RMZ) are recorded as stopping points inside their COA block section,
with their offset from the block's start. Branch blocks that leave the section (towards PDT, MVW,
GTLM, PLH, TEK, SUDV) are listed as boundary blocks for the boundary-flow stage (issue #12).
Codes are decoded through the mapping's ``value_maps`` and ``null_tokens``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from raildss_etl.mapping import REPO, MappingSpec, TableSpec, load_mapping
from raildss_params import Register, load
from raildss_schema.entities import BlockSection, BlockSectionLine, ConnectionEdge, OperationalPoint

M_PER_KM = 1000
DEFAULT_OUT = REPO / "data" / "interim" / "ktv-psa" / "topology"


class StoppingPoint(BaseModel):
    op_code: str
    offset_m: float


class Topology(BaseModel):
    network_version: str
    operational_points: list[OperationalPoint]
    block_sections: list[BlockSection]
    block_section_lines: list[BlockSectionLine]
    connection_edges: list[ConnectionEdge]
    stopping_points: dict[str, list[StoppingPoint]] = Field(description="COA block id -> halts inside it")
    boundary_blocks: list[str] = Field(description="block sections leaving the section (branches)")

    def content_hash(self) -> str:
        """SHA-256 of the canonical JSON of all entities (byte-identical builds -> same hash)."""
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Stage2Report(BaseModel):
    route_sum_km: str
    route_cum_km: str
    reverse_sum_km: str
    reverse_diff_km: float
    reverse_tolerance_km: float
    operational_points: int
    coa_blocks: int
    route_split_blocks: int
    block_section_lines: int
    connection_edges: int
    connection_edges_to_boundary: int
    empty_rows_dropped: dict[str, list[str]]
    route_blocks_missing: list[str]
    coa_blocks_without_lines: list[str]
    unresolved_connections: list[str]
    regime_violations: list[str]

    @property
    def ok(self) -> bool:
        return (
            Decimal(self.route_sum_km) == Decimal(self.route_cum_km)
            and self.reverse_diff_km <= self.reverse_tolerance_km
            and not self.route_blocks_missing
            and not self.coa_blocks_without_lines
            and not self.unresolved_connections
            and not self.regime_violations
        )


class Codec:
    """Decode source codes through the mapping's null tokens and value maps."""

    def __init__(self, mapping: MappingSpec) -> None:
        self.nulls = set(mapping.null_tokens)
        self.maps = mapping.value_maps

    def text(self, v: Any) -> str | None:
        s = str(v).strip() if v is not None and not (isinstance(v, float) and pd.isna(v)) else ""
        return None if s in self.nulls else s

    def decode(self, map_name: str, v: Any) -> Any:
        s = self.text(v)
        if s is None:
            return None
        table = self.maps[map_name]
        if s not in table:
            raise ValueError(f"unknown code {s!r} for value map {map_name!r}")
        return table[s]

    def number(self, v: Any) -> Decimal | None:
        s = self.text(v)
        return None if s is None else Decimal(s)


def _jsonable(v: Any) -> Any:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return None
    if isinstance(v, dt.date | dt.datetime | pd.Timestamp):
        return v.isoformat()
    return str(v)


def raw_fields(row: pd.Series, table: TableSpec) -> dict[str, Any]:
    return {c: _jsonable(row[c]) for c, s in table.columns.items() if s.targets == ["raw"] and c in row.index}


def _speed(kmh: Decimal | None, where: str) -> float:
    """Max speed in km/h. A blank, NA or non-positive value is a data error, not 0 km/h."""
    if kmh is None or kmh <= 0:
        raise ValueError(f"{where}: missing or non-positive MANMAXSPEED ({kmh})")
    return float(kmh)


def _metres(km: Decimal | None) -> float:
    if km is None:
        raise ValueError("missing distance")
    return float(km * M_PER_KM)


def _stopping_points(route: pd.DataFrame, codec: Codec) -> dict[str, list[StoppingPoint]]:
    """Halts inside a COA block.

    In route order, the rows sharing a COA block id are the block's start station followed by the
    halts inside it; a halt's offset is the running distance from the start station.
    """
    groups: dict[str, list[tuple[str, Decimal]]] = {}
    for _, row in route.iterrows():
        coa = codec.text(row["COABLCKSCTN"])
        if coa is not None:
            groups.setdefault(coa, []).append(
                (str(row["STATION_CODE"]), codec.number(row["DISTANCE"]) or Decimal(0))
            )
    out: dict[str, list[StoppingPoint]] = {}
    for coa, rows in groups.items():
        offset = Decimal(0)
        for i, (station, dist) in enumerate(rows):
            if i:
                out.setdefault(coa, []).append(StoppingPoint(op_code=station, offset_m=_metres(offset)))
            offset += dist
    return out


def build_topology(
    tables: dict[str, pd.DataFrame],
    mapping: MappingSpec | None = None,
    register: Register | None = None,
) -> tuple[Topology, Stage2Report]:
    spec = mapping or load_mapping()
    reg = register or load()
    codec = Codec(spec)
    nv = str(reg.value("network.version"))
    route_dn, route_up = tables["ROUTE_DN"], tables["ROUTE_UP"]

    # ---------------------------------------------------------------- DQ-15 distances
    route_sum = sum((codec.number(d) or Decimal(0) for d in route_dn["DISTANCE"]), Decimal(0))
    route_cum = codec.number(route_dn["CUM_DISTANCE"].iloc[-1]) or Decimal(0)
    reverse_sum = sum((codec.number(d) or Decimal(0) for d in route_up["DISTANCE"]), Decimal(0))

    # ---------------------------------------------------------------- operational points
    route_by_code = {str(r["STATION_CODE"]): r for _, r in route_dn.iterrows()}
    station_spec = spec.tables["STATION"]
    ops: list[OperationalPoint] = []
    for _, s in tables["STATION"].iterrows():
        code = str(s["MAVSTTNCODE"])
        r = route_by_code[code]
        km_post, sub_km = codec.number(s["MANMILEPOSTKM_I"]), codec.number(s["MANMILEPOSTSUBKM_I"])
        lat, lon = codec.number(s["MANLATITUDE"]), codec.number(s["MANLONGITUDE"])
        ops.append(
            OperationalPoint(
                network_version=nv,
                code=code,
                name=str(s["MAVSTTNNAME"]),
                seq=int(r["SEQ_NO"]),
                km=float(codec.number(r["CUM_DISTANCE"]) or 0),
                km_ref_hwh=None if km_post is None else float(km_post + (sub_km or Decimal(0)) / M_PER_KM),
                lat=None if lat is None else float(lat),
                lon=None if lon is None else float(lon),
                type=codec.decode("station_class", s["MACCLASSFLAG"]),
                is_junction=bool(codec.decode("yes_no", s["MACJUNCTION"])),
                is_halt=bool(codec.decode("yes_no", s["MACHALTFLAG"])),
                is_cabin=bool(codec.decode("yes_no", s["MACCABINFLAG"])),
                is_boundary=bool(codec.decode("yes_no", s["MACICFLAG"])),
                signalling=codec.decode("signalling", s["MAVSGNLARRNGMENT"]),
                interlocking_std=codec.text(s["MAVINTRLOCKSTD"]),
                block_instrument=codec.text(s["MAVBLOCKINSTTYPE"]),
                crossing_facility=bool(codec.decode("yes_no", s["MACPRCDCROSSFCLTY"])),
                traction_change=bool(codec.decode("yes_no", s["MACTRTNCHNG"])),
                crew_change=bool(codec.decode("yes_no", s["MACCREWCHNG"])),
                valid_from=s["MADVALIDFROM"],
                valid_to=s["MADVALIDTO"],
                raw=raw_fields(s, station_spec),
            )
        )
    ops.sort(key=lambda o: o.seq)
    section = {o.code for o in ops}
    km_of = {o.code: o.km for o in ops}

    # ---------------------------------------------------------------- block sections
    empty: dict[str, list[str]] = {"BLOCKSCTN": [], "BLOCKSCTNLINE": []}
    bs_spec = spec.tables["BLOCKSCTN"]
    blocks: list[BlockSection] = []
    boundary: list[str] = []
    for _, b in tables["BLOCKSCTN"].iterrows():
        bid = str(b["MAVBLCKSCTN"])
        if codec.text(b["MANINTRDIST"]) is None and codec.text(b["MAVSGNLTYPE"]) is None:
            empty["BLOCKSCTN"].append(bid)
            continue
        ends = bid.split("-")
        if not all(e in section for e in ends):
            boundary.append(bid)
            continue
        blocks.append(
            BlockSection(
                network_version=nv,
                id=bid,
                from_op=str(b["MAVFROMSTTNCODE"]),
                to_op=str(b["MAVTOSTTNCODE"]),
                level=codec.decode("coa_level", b["MANCOABLCKSCTN"]),
                direction=codec.decode("line_direction", b["MAVDRTN"]),
                n_lines=int(b["MANNUMBLINES"]),
                system=codec.decode("block_system", b["MAVSGNLTYPE"]),
                length_m=_metres(codec.number(b["MANINTRDIST"])),
                vmax_kmh=_speed(codec.number(b["MANMAXSPEED"]), f"BLOCKSCTN {bid}"),
                controller_section=codec.text(b["MAVSCTNCODE"]),
                raw=raw_fields(b, bs_spec),
            )
        )
    blocks.sort(key=lambda x: (x.level, x.id))
    coa_ids = {b.id for b in blocks if b.level == "coa"}

    # ---------------------------------------------------------------- block section lines
    bsl_spec = spec.tables["BLOCKSCTNLINE"]
    lines: list[BlockSectionLine] = []
    line_id_of: dict[tuple[str, str], str] = {}
    for _, row in tables["BLOCKSCTNLINE"].iterrows():
        bid, line_id = str(row["MAVBLCKSCTN"]), codec.text(row["MAVLINENUMB"])
        if line_id is None:
            empty["BLOCKSCTNLINE"].append(bid)
            continue
        line_id_of[(bid, str(row["MANSEQNUMB"]))] = line_id
        if bid not in coa_ids:
            continue
        lines.append(
            BlockSectionLine(
                network_version=nv,
                block_section_id=bid,
                line_id=line_id,
                direction=codec.decode("line_direction", line_id),
                length_m=_metres(codec.number(row["MANLINELENGTH"])),
                system=codec.decode("block_system", row["MAVSGNLTYPE"]),
                vmax_kmh=_speed(codec.number(row["MANMAXSPEED"]), f"BLOCKSCTNLINE {bid}/{line_id}"),
                category=codec.text(row["MAVLINECATEGORY"]),
                raw=raw_fields(row, bsl_spec),
            )
        )
    lines.sort(key=lambda x: (x.block_section_id, x.line_id))

    # ---------------------------------------------------------------- connections
    station_lines = {(str(r["MAVSTTNCODE"]), str(r["MANSEQNUMB"])) for _, r in tables["STTNLINE"].iterrows()}
    con_spec = spec.tables["CONNECTIONS"]
    edges: list[ConnectionEdge] = []
    unresolved: list[str] = []
    for _, c in tables["CONNECTIONS"].iterrows():
        op, sl_no, bid, bl_no = (
            str(c[k]) for k in ("MAVSTTNCODE", "MANSTTNLINENUMB", "MAVBLCKSCTN", "MANBSLINENUMB")
        )
        key = f"{op}/{sl_no} <-> {bid}/{bl_no}"
        if (op, sl_no) not in station_lines:
            unresolved.append(f"{key}: station line not in STTNLINE")
            continue
        if (bid, bl_no) not in line_id_of:
            unresolved.append(f"{key}: block line not in BLOCKSCTNLINE")
            continue
        edges.append(
            ConnectionEdge(
                network_version=nv,
                op_code=op,
                station_line_no=int(sl_no),
                block_section_id=bid,
                block_line_id=line_id_of[(bid, bl_no)],
                mode=codec.decode("recv_send", c["MACRECVSENDFLAG"]),
                raw=raw_fields(c, con_spec),
            )
        )
    edges.sort(key=lambda e: (e.op_code, e.station_line_no, e.block_section_id, e.block_line_id, e.mode))

    # ---------------------------------------------------------------- checks
    block_ids = {str(b) for b in tables["BLOCKSCTN"]["MAVBLCKSCTN"]}
    route_refs = {
        x
        for df in (route_dn, route_up)
        for col in ("BLOCK_SECTION", "COABLCKSCTN")
        for x in (codec.text(v) for v in df[col])
        if x is not None
    }
    lines_per_block: dict[str, int] = {}
    for ln in lines:
        lines_per_block[ln.block_section_id] = lines_per_block.get(ln.block_section_id, 0) + 1
    vzm_km = km_of["VZM"]
    regimes = reg.value("block.system")  # {ktv_vzm: {system: AT, lines: 3}, vzm_psa: {...}}
    regime: list[str] = []
    for blk in blocks:
        if blk.level != "coa":
            continue
        ktv_vzm = max(km_of[blk.from_op], km_of[blk.to_op]) <= vzm_km
        want = regimes["ktv_vzm" if ktv_vzm else "vzm_psa"]
        want_system, want_lines = codec.decode("block_system", want["system"]), int(want["lines"])
        n_rows = lines_per_block.get(blk.id, 0)
        if blk.system != want_system or blk.n_lines != want_lines or n_rows != want_lines:
            regime.append(f"{blk.id}: {blk.system}/{blk.n_lines} lines/{n_rows} line rows")

    stops = _stopping_points(route_dn, codec)
    for coa, pts in _stopping_points(route_up, codec).items():
        stops.setdefault(coa, []).extend(pts)
    topology = Topology(
        network_version=nv,
        operational_points=ops,
        block_sections=blocks,
        block_section_lines=lines,
        connection_edges=edges,
        stopping_points=dict(sorted(stops.items())),
        boundary_blocks=sorted(boundary),
    )
    report = Stage2Report(
        route_sum_km=str(route_sum),
        route_cum_km=str(route_cum),
        reverse_sum_km=str(reverse_sum),
        reverse_diff_km=float(abs(route_sum - reverse_sum)),
        reverse_tolerance_km=float(reg.value("dq.route_reverse_tolerance_km")),
        operational_points=len(ops),
        coa_blocks=len(coa_ids),
        route_split_blocks=sum(1 for b in blocks if b.level == "route_split_at_halt"),
        block_section_lines=len(lines),
        connection_edges=len(edges),
        connection_edges_to_boundary=sum(1 for e in edges if e.block_section_id in set(boundary)),
        empty_rows_dropped=empty,
        route_blocks_missing=sorted(route_refs - block_ids),
        coa_blocks_without_lines=sorted(coa_ids - set(lines_per_block)),
        unresolved_connections=unresolved,
        regime_violations=regime,
    )
    return topology, report


def write_topology(topology: Topology, report: Stage2Report, out_dir: Path = DEFAULT_OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("operational_points", "block_sections", "block_section_lines", "connection_edges"):
        items = getattr(topology, name)
        (out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(i.model_dump(mode="json"), sort_keys=True) + "\n" for i in items),
            encoding="utf-8",
        )
    extra = {
        "network_version": topology.network_version,
        "content_sha256": topology.content_hash(),
        "stopping_points": {k: [p.model_dump() for p in v] for k, v in topology.stopping_points.items()},
        "boundary_blocks": topology.boundary_blocks,
        "report": report.model_dump(mode="json"),
        "ok": report.ok,
    }
    (out_dir / "topology_report.json").write_text(
        json.dumps(extra, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
