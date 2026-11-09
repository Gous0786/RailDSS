"""Rule predicates of the catalogue: pure ``function(ctx) -> list[Violation] | None`` (spec §9.2)."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from raildss_verifier.model import TrainPlan
from raildss_verifier.network import Network

KMH_PER_MPS = 3.6
SECONDS_PER_MIN = 60


@dataclass(frozen=True)
class Unit:
    """One train on one unit resource: a block line (absolute block) or a sub-block (automatic)."""

    key: str
    automatic: bool
    run_id: str
    length_m: float
    direction: str
    enter_s: float
    clear_s: float


def call_directions(net: Network, train: TrainPlan, i: int) -> set[str]:
    """Running directions of the train at ``calls[i]`` (from the runs it arrives by and leaves on)."""
    out = set()
    for j in (i - 1, i):
        if 0 <= j < len(train.runs) and train.runs[j].block_id in net.blocks:
            out.add(net.blocks[train.runs[j].block_id].direction)
    return out


def units(net: Network, trains: list[TrainPlan]) -> Iterator[Unit]:
    for t in trains:
        for run in t.runs:
            line = net.lines.get((run.block_id, run.line_id))
            if line is None:
                continue
            direction = net.blocks[run.block_id].direction
            if run.sub_blocks:
                for sb in run.sub_blocks:
                    key = net.sub_block_key(run.block_id, run.line_id, sb.seq)
                    yield Unit(key, True, t.run_id, t.length_m, direction, sb.enter_s, sb.clear_s)
            else:
                key = net.segment(run.block_id, run.line_id)
                auto = line.system == "AUTO"
                yield Unit(key, auto, t.run_id, t.length_m, direction, run.enter_s, run.clear_s)


def by_key(items: Iterator[Unit]) -> dict[str, list[Unit]]:
    out: dict[str, list[Unit]] = {}
    for u in items:
        out.setdefault(u.key, []).append(u)
    for v in out.values():
        v.sort(key=lambda u: (u.enter_s, u.run_id))
    return out


def overlaps(a_start: float, a_end: float, b_start: float, b_end: float, tol: float) -> bool:
    return a_start < b_end - tol and b_start < a_end - tol
