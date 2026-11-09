"""The network facts the rules need, read independently of the ETL and the twin (NFR-MNT-02).

``load`` reads a published network directory (``data/published/<version>``) with the canonical
schema models only. Tests build a :class:`Network` directly.

Physical identity. Block ids are directed (``A-B`` and ``B-A``); both directions of a pair share
their lines, so a line's physical key is ``(sorted pair, line_id)``. Sub-blocks are numbered in the
travel order of their block id; sub-block ``k`` of ``n`` on the UP id is ``n + 1 - k`` on the DN id.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from raildss_params.register import default_path
from raildss_schema.entities import (
    BlockSection,
    BlockSectionLine,
    ConnectionEdge,
    OperationalPoint,
    Platform,
    StationLine,
    SubBlock,
)

PUBLISHED_DIR = default_path().parent.parent / "data" / "published"
DN = "DN"
BOTH = "BOTH"


@dataclass(frozen=True)
class Station:
    code: str
    km: float
    crossing_facility: bool


@dataclass(frozen=True)
class Block:
    id: str
    from_op: str
    to_op: str
    direction: str  # DN | UP
    system: str  # ABS_absolute | AUTO | ...


@dataclass(frozen=True)
class Line:
    """A block-section line."""

    block_id: str
    line_id: str
    direction: str  # DN | UP | BOTH
    system: str
    length_m: float
    vmax_kmh: float


@dataclass(frozen=True)
class Track:
    """A station line."""

    op: str
    line_no: int
    kind: str
    directions: str  # DN | UP | BOTH
    csr_m: float | None = None
    capacity: int = 1
    platform: bool = False
    platform_length_m: float | None = None
    electrified: bool | None = None


@dataclass
class Network:
    version: str
    stations: dict[str, Station]
    blocks: dict[str, Block]
    lines: dict[tuple[str, str], Line]  # (block id, line id)
    tracks: dict[tuple[str, int], Track]  # (op, line no)
    connections: set[tuple[str, int, str, str, str]]  # (op, line no, block id, line id, receive|send)
    sub_blocks: dict[tuple[str, str], int] = field(default_factory=dict)  # (block id, line id) -> count

    def segment(self, block_id: str, line_id: str) -> str:
        b = self.blocks[block_id]
        return f"{'~'.join(sorted((b.from_op, b.to_op)))}/{line_id}"

    def sub_block_key(self, block_id: str, line_id: str, seq: int) -> str:
        n = self.sub_blocks.get((block_id, line_id), 0)
        canonical = seq if self.blocks[block_id].direction == DN else n + 1 - seq
        return f"{self.segment(block_id, line_id)}#{canonical}"

    def sub_block_seq(self, block_id: str, line_id: str, canonical: int) -> int:
        """Inverse of :meth:`sub_block_key`: the block id's own sequence number of a physical sub-block."""
        n = self.sub_blocks.get((block_id, line_id), 0)
        return canonical if self.blocks[block_id].direction == DN else n + 1 - canonical


def _rows(path: Path, model: Any) -> list[Any]:
    data = json.loads((path / f"{model.__table__}.json").read_text(encoding="utf-8"))
    return list(TypeAdapter(list[model]).validate_python(data))


def load(version: str, root: Path | None = None) -> Network:
    """Read the published network ``version`` (block stations, COA blocks, lines, Connections)."""
    path = (root or PUBLISHED_DIR) / version
    ops: list[OperationalPoint] = _rows(path, OperationalPoint)
    blocks: list[BlockSection] = [b for b in _rows(path, BlockSection) if b.level == "coa"]
    block_ids = {b.id for b in blocks}
    lines: list[BlockSectionLine] = _rows(path, BlockSectionLine)
    platforms: dict[tuple[str, int], float] = {}
    for p in _rows(path, Platform):
        assert isinstance(p, Platform)
        if p.station_line_no is not None and p.length_m is not None:
            key = (p.op_code, p.station_line_no)
            platforms[key] = max(platforms.get(key, 0.0), p.length_m)
    tracks: dict[tuple[str, int], Track] = {}
    for s in _rows(path, StationLine):
        assert isinstance(s, StationLine)
        if s.status != "in_use":
            continue
        tracks[(s.op_code, s.line_no)] = Track(
            op=s.op_code,
            line_no=s.line_no,
            kind=s.kind,
            directions=s.directions,
            csr_m=s.csr_m,
            capacity=s.capacity_trains,
            platform=s.platform,
            platform_length_m=platforms.get((s.op_code, s.line_no)),
            electrified=s.electrified,
        )
    edges: list[ConnectionEdge] = _rows(path, ConnectionEdge)
    subs: dict[tuple[str, str], int] = {}
    for sb in _rows(path, SubBlock):
        sub_key = (sb.block_section_id, sb.line_id)
        subs[sub_key] = subs.get(sub_key, 0) + 1
    return Network(
        version=version,
        stations={o.code: Station(o.code, o.km, o.crossing_facility) for o in ops if not o.is_halt},
        blocks={b.id: Block(b.id, b.from_op, b.to_op, b.direction, b.system) for b in blocks},
        lines={
            (ln.block_section_id, ln.line_id): Line(
                ln.block_section_id, ln.line_id, ln.direction, ln.system, ln.length_m, ln.vmax_kmh
            )
            for ln in lines
            if ln.block_section_id in block_ids
        },
        tracks=tracks,
        connections={
            (e.op_code, e.station_line_no, e.block_section_id, e.block_line_id, e.mode) for e in edges
        },
        sub_blocks=subs,
    )
