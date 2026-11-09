"""Block-section rules: absolute block, adequate distance, sub-blocks, single line, speed, maintenance."""

from __future__ import annotations

import itertools

from raildss_verifier.engine import Context
from raildss_verifier.model import Violation
from raildss_verifier.network import BOTH
from raildss_verifier.rules import KMH_PER_MPS, SECONDS_PER_MIN, Unit, by_key, overlaps, units


def _one_train(ctx: Context, automatic: bool) -> list[Violation]:
    tol = float(ctx.param("ver.time_tolerance_s"))
    out: list[Violation] = []
    for key, us in by_key(u for u in units(ctx.net, ctx.plan.trains) if u.automatic == automatic).items():
        for i, a in enumerate(us):
            for b in us[i + 1 :]:
                if b.enter_s >= a.clear_s - tol:
                    break
                if b.run_id != a.run_id:
                    out.append(
                        Violation(
                            trains=[a.run_id, b.run_id],
                            time_s=b.enter_s,
                            detail=f"{b.run_id} enters {key} at {b.enter_s:.0f} s while {a.run_id} "
                            f"occupies it until {a.clear_s:.0f} s",
                        )
                    )
    return out


def one_train_per_block(ctx: Context) -> list[Violation]:
    """VER-BLK-01: at most one train in an absolute-block section line at any time."""
    return _one_train(ctx, automatic=False)


def one_train_per_sub_block(ctx: Context) -> list[Violation]:
    """VER-HDW-01: at most one train in an automatic-signal sub-block at any time."""
    return _one_train(ctx, automatic=True)


def _required_gap_s(ctx: Context, prev: Unit) -> float:
    adequate = float(ctx.param("block.adequate_distance_m.macls"))
    speed = float(ctx.param("ver.clearing_speed_kmh")) / KMH_PER_MPS
    return (prev.length_m + adequate) / speed


def _successions(ctx: Context) -> list[tuple[str, Unit, Unit, float]]:
    """``(unit key, previous, next, spare s)``: gap after clearing minus the adequate-distance time."""
    out = []
    for key, us in by_key(units(ctx.net, ctx.plan.trains)).items():
        for a, b in itertools.pairwise(us):
            if a.run_id != b.run_id:
                out.append((key, a, b, b.enter_s - a.clear_s - _required_gap_s(ctx, a)))
    return out


def adequate_distance(ctx: Context) -> list[Violation]:
    """VER-BLK-02: line clear only after the previous train cleared the section plus adequate distance."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    return [
        Violation(
            trains=[a.run_id, b.run_id],
            time_s=b.enter_s,
            detail=f"{b.run_id} enters {key} {-spare:.0f} s before {a.run_id} has cleared it plus "
            "adequate distance",
        )
        for key, a, b, spare in _successions(ctx)
        if spare < -tol
    ]


def separation_buffer(ctx: Context) -> list[Violation]:
    """VER-BUF-01: the spare time behind the previous train is at least the warning buffer."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    buffer_s = float(ctx.param("ver.buffer_warn_min")) * SECONDS_PER_MIN
    return [
        Violation(
            trains=[a.run_id, b.run_id],
            time_s=b.enter_s,
            detail=f"{b.run_id} follows {a.run_id} on {key} with {spare:.0f} s spare (< {buffer_s:.0f} s)",
        )
        for key, a, b, spare in _successions(ctx)
        if spare < buffer_s - tol
    ]


def no_opposing_on_single_line(ctx: Context) -> list[Violation]:
    """VER-BLK-04: on a single (bidirectional) line, no opposing trains in the same block section."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    runs = []
    for t in ctx.plan.trains:
        for r in t.runs:
            line = ctx.net.lines.get((r.block_id, r.line_id))
            if line is not None and line.direction == BOTH:
                seg = ctx.net.segment(r.block_id, r.line_id)
                runs.append((seg, ctx.net.blocks[r.block_id].direction, t.run_id, r.enter_s, r.clear_s))
    out = []
    for i, (seg, d, tid, s, e) in enumerate(runs):
        for seg2, d2, tid2, s2, e2 in runs[i + 1 :]:
            if seg == seg2 and d != d2 and tid != tid2 and overlaps(s, e, s2, e2, tol):
                out.append(
                    Violation(
                        trains=sorted((tid, tid2)),
                        time_s=max(s, s2),
                        detail=f"{tid} ({d}) and {tid2} ({d2}) both on single line {seg}",
                    )
                )
    return out


def line_speed(ctx: Context) -> list[Violation]:
    """VER-LSP-01: the average speed over a block line never exceeds its maximum speed (to the running-time
    resolution: plans state running times rounded to whole seconds)."""
    tol = float(ctx.param("ver.running_time_resolution_s"))
    out = []
    for t in ctx.plan.trains:
        for r in t.runs:
            line = ctx.net.lines.get((r.block_id, r.line_id))
            if line is None:
                continue
            min_s = line.length_m / (line.vmax_kmh / KMH_PER_MPS)
            if r.clear_s - r.enter_s < min_s - tol:
                kmh = line.length_m / max(r.clear_s - r.enter_s, tol) * KMH_PER_MPS
                out.append(
                    Violation(
                        trains=[t.run_id],
                        time_s=r.enter_s,
                        detail=f"{t.run_id} averages {kmh:.0f} km/h on {r.block_id}/{r.line_id} "
                        f"(max {line.vmax_kmh:.0f})",
                    )
                )
    return out


def maintenance_block(ctx: Context) -> list[Violation] | None:
    """VER-MNT-01: no train in a block section during its maintenance or power block."""
    if not ctx.plan.maintenance:
        return None
    tol = float(ctx.param("ver.time_tolerance_s"))
    out = []
    for m in ctx.plan.maintenance:
        if m.block_id not in ctx.net.blocks:
            raise KeyError(f"maintenance block on unknown block section {m.block_id}")
        mb = ctx.net.blocks[m.block_id]
        pair = sorted((mb.from_op, mb.to_op))
        for t in ctx.plan.trains:
            for r in t.runs:
                b = ctx.net.blocks.get(r.block_id)
                if b is None or sorted((b.from_op, b.to_op)) != pair:
                    continue
                if m.line_id not in (None, r.line_id):
                    continue
                if overlaps(r.enter_s, r.clear_s, m.start_s, m.end_s, tol):
                    out.append(
                        Violation(
                            trains=[t.run_id],
                            time_s=r.enter_s,
                            detail=f"{t.run_id} on {r.block_id}/{r.line_id} during the block "
                            f"{m.start_s:.0f}-{m.end_s:.0f} s",
                        )
                    )
    return out
