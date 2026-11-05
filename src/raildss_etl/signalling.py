"""Completion: KTV-VZM automatic sub-blocks, twin signals and station routes (issue #13).

Spec FR-TWIN-04, REC-07, OD-32. Everything here is synthetic (``source = synthetic``) and used by
the twin only; the dataset has no signal data (R06 §4.4).

* Automatic block lines (KTV-VZM, system AUTO) are tiled into equal sub-blocks whose length is as
  close as possible to ``abs.subblock_spacing_km.ktv_vzm`` (DUMMY, 1.0 km by default). Equal tiling
  makes the tiling exact by construction; the validator still checks it.
* Signals: a starter at the start of every block line, an automatic signal at every interior
  sub-block boundary, and a home signal at the end of every block line.
* Station routes: one record per Connections edge (receive: home -> station line; send:
  station line -> starter), with the overlap from ``block.adequate_distance_m.macls``.
* Braking check: with N-aspect signalling a train is warned (N - 2) sub-blocks ahead, so
  (N - 2) x shortest sub-block must cover the braking distance of the fastest train at line speed.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from raildss_etl.topology import Topology
from raildss_params import Register, load
from raildss_schema.base import Location
from raildss_schema.entities import ConnectionEdge, Signal, StationLine, SubBlock

M_PER_KM = 1000
KMH_PER_MPS = 3.6
TOLERANCE_M = 1e-6


class StationRoute(BaseModel):
    """Meso station route derived from a Connections edge (twin-only, synthetic)."""

    op_code: str
    station_line_no: int
    block_section_id: str
    block_line_id: str
    mode: str
    entry_signal_id: str
    exit_signal_id: str
    overlap_m: float


class Signalling(BaseModel):
    spacing_km: float
    sub_blocks: list[SubBlock]
    signals: list[Signal]
    routes: list[StationRoute]


def _sid(kind: str, block: str, line: str, seq: int = 0) -> str:
    return f"{kind}-{block}-{line}" + (f"-{seq}" if seq else "")


def build_signalling(
    topology: Topology,
    station_lines: list[StationLine],
    register: Register | None = None,
    spacing_km: float | None = None,
) -> Signalling:
    reg = register or load()
    spacing = float(spacing_km if spacing_km is not None else reg.value("abs.subblock_spacing_km.ktv_vzm"))
    overlap = float(reg.value("block.adequate_distance_m.macls"))
    nv = topology.network_version
    km = {o.code: o.km for o in topology.operational_points}
    blocks = {b.id: b for b in topology.block_sections if b.level == "coa"}

    sub_blocks: list[SubBlock] = []
    signals: list[Signal] = []
    for line in topology.block_section_lines:
        block = blocks[line.block_section_id]
        sign = 1 if km[block.to_op] > km[block.from_op] else -1

        def loc(
            offset: float,
            line_id: str = line.line_id,
            b: str = block.id,
            start: float = km[block.from_op],
            sgn: int = sign,
        ) -> Location:
            return Location(
                ne_id=f"{b}/{line_id}", offset_m=offset, km=round(start + sgn * offset / M_PER_KM, 6)
            )

        common: dict[str, Any] = {
            "network_version": nv,
            "direction": block.direction,
            "block_section_id": block.id,
            "line_id": line.line_id,
            "source": "synthetic",
        }
        signals.append(
            Signal(
                id=_sid("ST", block.id, line.line_id),
                type="starter",
                op_code=block.from_op,
                location=loc(0.0),
                **common,
            )
        )
        signals.append(
            Signal(
                id=_sid("HM", block.id, line.line_id),
                type="home",
                op_code=block.to_op,
                location=loc(line.length_m),
                **common,
            )
        )
        if line.system != "AUTO":
            continue
        n = max(1, round(line.length_m / (spacing * M_PER_KM)))
        size = line.length_m / n
        for i in range(n):
            start, end = i * size, line.length_m if i == n - 1 else (i + 1) * size
            entry = (
                _sid("ST", block.id, line.line_id) if i == 0 else _sid("AU", block.id, line.line_id, i + 1)
            )
            if i:
                signals.append(
                    Signal(
                        id=entry,
                        type="automatic",
                        location=loc(start),
                        aspects=int(reg.value("twin.abs_signal_aspects")),
                        **common,
                    )
                )
            sub_blocks.append(
                SubBlock(
                    network_version=nv,
                    block_section_id=block.id,
                    line_id=line.line_id,
                    seq=i + 1,
                    from_offset_m=start,
                    to_offset_m=end,
                    entry_signal_id=entry,
                )
            )

    lines = {(s.op_code, s.line_no) for s in station_lines}
    routes = [
        _route(e, overlap) for e in topology.connection_edges if (e.op_code, e.station_line_no) in lines
    ]
    return Signalling(spacing_km=spacing, sub_blocks=sub_blocks, signals=signals, routes=routes)


def _route(edge: ConnectionEdge, overlap: float) -> StationRoute:
    line_sig = f"LN-{edge.op_code}-{edge.station_line_no}"
    if edge.mode == "receive":
        entry, exit_ = _sid("HM", edge.block_section_id, edge.block_line_id), line_sig
    else:
        entry, exit_ = line_sig, _sid("ST", edge.block_section_id, edge.block_line_id)
    return StationRoute(
        op_code=edge.op_code,
        station_line_no=edge.station_line_no,
        block_section_id=edge.block_section_id,
        block_line_id=edge.block_line_id,
        mode=edge.mode,
        entry_signal_id=entry,
        exit_signal_id=exit_,
        overlap_m=overlap,
    )


def validate(signalling: Signalling, topology: Topology, register: Register | None = None) -> list[str]:
    """Tiling (no gap, no overlap, exact ends), braking cover and route coverage problems."""
    reg = register or load()
    aspects = int(reg.value("twin.abs_signal_aspects"))
    decel = float(reg.value("brake.decel_mps2")["passenger"])  # fastest consist at line speed
    problems: list[str] = []
    by_line: dict[tuple[str, str], list[SubBlock]] = {}
    for sb in signalling.sub_blocks:
        by_line.setdefault((sb.block_section_id, sb.line_id), []).append(sb)
    for line in topology.block_section_lines:
        if line.system != "AUTO":
            continue
        key = (line.block_section_id, line.line_id)
        parts = sorted(by_line.get(key, []), key=lambda s: s.from_offset_m)
        if not parts:
            problems.append(f"{key}: no sub-blocks")
            continue
        cursor = 0.0
        for sb in parts:
            if abs(sb.from_offset_m - cursor) > TOLERANCE_M:
                kind = "gap" if sb.from_offset_m > cursor else "overlap"
                problems.append(f"{key}: {kind} at {cursor:.1f} m")
            cursor = sb.to_offset_m
        if abs(cursor - line.length_m) > TOLERANCE_M:
            problems.append(f"{key}: tiling ends at {cursor:.1f} m, line is {line.length_m:.1f} m")
        v = line.vmax_kmh / KMH_PER_MPS
        braking = v * v / (2 * decel)
        shortest = min(sb.to_offset_m - sb.from_offset_m for sb in parts)
        if (aspects - 2) * shortest < braking:
            problems.append(
                f"{key}: warning distance {(aspects - 2) * shortest:.0f} m < braking {braking:.0f} m"
            )
    edges = len(topology.connection_edges)
    if len(signalling.routes) != edges:
        problems.append(f"routes: {len(signalling.routes)} for {edges} Connections edges")
    return problems
