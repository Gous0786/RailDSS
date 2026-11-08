"""The published network as the twin sees it (spec FR-TWIN-10; issue #20).

Built from the entities of a published network version (:mod:`raildss_etl.publish`): COA block
sections with their lines, automatic-block sub-blocks, station lines and the Connections edges that
say which block line may feed which station line.

A block id names the direction of travel (``KUK-VZM`` runs KUK to VZM, down). The physical line is
shared between the two directions, so a unit resource is keyed by the unordered station pair and the
line id: the MID line of ``KUK-VZM`` and of ``VZM-KUK`` are one resource. Sub-blocks are numbered
from the start of their block id; the canonical index runs in the DOWN (increasing chainage)
direction, so an UP block's sub-block ``s`` of ``n`` is canonical index ``n + 1 - s``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from raildss_schema.base import Stamped
from raildss_schema.entities import (
    BlockSection,
    BlockSectionLine,
    ConnectionEdge,
    OperationalPoint,
    StationLine,
    SubBlock,
)

Direction = str  # "DN" | "UP"


def pair_key(a: str, b: str) -> str:
    """Unordered station pair, the physical segment of a block section."""
    return "~".join(sorted((a, b)))


@dataclass(frozen=True)
class BlockLine:
    """One line of a COA block section, as traversed in the block id's direction."""

    block_id: str
    line_id: str
    direction: str  # DN / UP / BOTH
    system: str
    length_m: float
    segment: str  # pair_key of the two stations

    @property
    def key(self) -> str:
        """Physical resource of the whole line (absolute block)."""
        return f"{self.segment}/{self.line_id}"


@dataclass
class Layout:
    network_version: str
    ops: dict[str, OperationalPoint]
    order: list[str]  # operational point codes by chainage
    blocks: dict[str, BlockSection]  # COA level, by id
    block_by_pair: dict[tuple[str, str], BlockSection]  # (from_op, to_op) -> block
    lines: dict[str, list[BlockLine]]  # block id -> lines
    station_lines: dict[str, list[StationLine]]  # op -> lines by number
    receive: dict[tuple[str, int], set[tuple[str, str]]] = field(default_factory=dict)
    send: dict[tuple[str, int], set[tuple[str, str]]] = field(default_factory=dict)
    sub_blocks: dict[tuple[str, str], list[SubBlock]] = field(default_factory=dict)
    halts_in: dict[str, list[str]] = field(default_factory=dict)  # block id -> halts inside, travel order
    signals: dict[tuple[str, str, int], str] = field(
        default_factory=dict
    )  # (block, line, seq) -> entry signal

    def km(self, op: str) -> float:
        return self.ops[op].km

    def direction(self, a: str, b: str) -> str:
        return "DN" if self.km(b) > self.km(a) else "UP"

    def block_stations(self) -> list[str]:
        return [c for c in self.order if not self.ops[c].is_halt]

    def hop_block(self, a: str, b: str) -> BlockSection:
        """The COA block section from ``a`` to ``b`` (adjacent block stations)."""
        return self.block_by_pair[(a, b)]

    def lines_for(self, block_id: str, direction: str) -> list[BlockLine]:
        """Lines a train may use in ``direction``: own direction first, then BOTH (MID)."""
        usable = [x for x in self.lines[block_id] if x.direction in (direction, "BOTH")]
        return sorted(usable, key=lambda x: (x.direction != direction, x.line_id))

    def canonical_sub_blocks(self, block_id: str, line_id: str) -> list[tuple[str, float]]:
        """``(resource key, length_m)`` of the sub-blocks of a line, in travel order of ``block_id``."""
        subs = sorted(self.sub_blocks.get((block_id, line_id), []), key=lambda s: s.seq)
        n = len(subs)
        block = self.blocks[block_id]
        segment = pair_key(block.from_op, block.to_op)
        out = []
        for s in subs:
            canonical = s.seq if block.direction == "DN" else n + 1 - s.seq
            out.append((f"{segment}/{line_id}#{canonical}", s.to_offset_m - s.from_offset_m))
        return out

    def entry_signal(self, block_id: str, line_id: str, seq: int) -> str | None:
        return self.signals.get((block_id, line_id, seq))


def build_layout(tables: Mapping[str, Sequence[Stamped]]) -> Layout:
    """Assemble a :class:`Layout` from the published tables."""
    ops_list: list[OperationalPoint] = sorted(_rows(tables, "operational_point"), key=lambda o: o.km)
    ops = {o.code: o for o in ops_list}
    all_blocks: list[BlockSection] = _rows(tables, "block_section")
    coa = [b for b in all_blocks if b.level == "coa"]
    blocks = {b.id: b for b in coa}
    lines: dict[str, list[BlockLine]] = defaultdict(list)
    for ln in _rows(tables, "block_section_line"):
        b = blocks.get(ln.block_section_id)
        if b is None:
            continue
        assert isinstance(ln, BlockSectionLine)
        lines[b.id].append(
            BlockLine(b.id, ln.line_id, ln.direction, ln.system, ln.length_m, pair_key(b.from_op, b.to_op))
        )
    station_lines: dict[str, list[StationLine]] = defaultdict(list)
    for sl in _rows(tables, "station_line"):
        station_lines[sl.op_code].append(sl)
    for v in station_lines.values():
        v.sort(key=lambda s: s.line_no)
    layout = Layout(
        network_version=ops_list[0].network_version,
        ops=ops,
        order=[o.code for o in ops_list],
        blocks=blocks,
        block_by_pair={(b.from_op, b.to_op): b for b in coa},
        lines=dict(lines),
        station_lines=dict(station_lines),
    )
    receive: dict[tuple[str, int], set[tuple[str, str]]] = defaultdict(set)
    send: dict[tuple[str, int], set[tuple[str, str]]] = defaultdict(set)
    for e in _rows(tables, "connection_edge"):
        assert isinstance(e, ConnectionEdge)
        (receive if e.mode == "receive" else send)[(e.op_code, e.station_line_no)].add(
            (e.block_section_id, e.block_line_id)
        )
    layout.receive, layout.send = dict(receive), dict(send)
    subs: dict[tuple[str, str], list[SubBlock]] = defaultdict(list)
    for sb in _rows(tables, "sub_block"):
        subs[(sb.block_section_id, sb.line_id)].append(sb)
        if sb.entry_signal_id is not None:
            layout.signals[(sb.block_section_id, sb.line_id, sb.seq)] = sb.entry_signal_id
    layout.sub_blocks = dict(subs)
    # Halts sit inside the COA block that spans their chainage, in the block's travel order.
    for b in coa:
        lo, hi = sorted((ops[b.from_op].km, ops[b.to_op].km))
        inside = [o.code for o in ops_list if o.is_halt and lo < o.km < hi]
        layout.halts_in[b.id] = inside if b.direction == "DN" else inside[::-1]
    return layout


def _rows(tables: Mapping[str, Sequence[Stamped]], name: str) -> list[Any]:
    return list(tables.get(name, ()))
