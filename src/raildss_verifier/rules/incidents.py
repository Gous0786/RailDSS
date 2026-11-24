"""Rules for incidents in force (issue #151; DSC-08, DSC-24): speed restrictions (TSR, caution working),
single-line working (head-on and crossing) and closed section exits. Each rule applies only when the plan
context declares the incident, and is otherwise not applicable."""

from __future__ import annotations

import itertools

from raildss_verifier.engine import Context
from raildss_verifier.model import BlockRun, Violation
from raildss_verifier.rules import KMH_PER_MPS, overlaps

M_PER_KM = 1000.0


def _metres(ctx: Context, r: BlockRun) -> float:
    b = ctx.net.blocks[r.block_id]
    return abs(ctx.net.stations[b.to_op].km - ctx.net.stations[b.from_op].km) * M_PER_KM


def _pair(ctx: Context, block_id: str) -> tuple[str, ...]:
    b = ctx.net.blocks[block_id]
    return tuple(sorted((b.from_op, b.to_op)))


def speed_restriction(ctx: Context) -> list[Violation] | None:
    """VER-SPD-01: a train entering a block line under a speed restriction runs no faster than the limit
    (plus the paper-line-clear wait of caution working), to the running-time resolution."""
    if not ctx.plan.restrictions:
        return None
    tol = float(ctx.param("ver.running_time_resolution_s"))
    out = []
    for rs in ctx.plan.restrictions:
        if rs.block_id not in ctx.net.blocks:
            raise KeyError(f"speed restriction on unknown block section {rs.block_id}")
        pair = _pair(ctx, rs.block_id)
        for t in ctx.plan.trains:
            for r in t.runs:
                if r.block_id not in ctx.net.blocks or _pair(ctx, r.block_id) != pair:
                    continue
                if rs.line_id not in (None, r.line_id) or not rs.start_s <= r.enter_s < rs.end_s:
                    continue
                need = _metres(ctx, r) / (rs.speed_kmh / KMH_PER_MPS) + rs.extra_s
                took = r.clear_s - r.enter_s
                if took < need - tol:
                    out.append(
                        Violation(
                            trains=[t.run_id],
                            time_s=r.enter_s,
                            detail=f"{t.run_id} runs {r.block_id}/{r.line_id} in {took:.0f} s under a "
                            f"{rs.speed_kmh:g} km/h restriction (at least {need:.0f} s)",
                        )
                    )
    return out


def _working_runs(ctx: Context) -> list[tuple[int, str, str, BlockRun]]:
    """Runs on a section under single-line working while it lasts: (working index, train, direction, run)."""
    out = []
    for k, w in enumerate(ctx.plan.single_line):
        if w.block_id not in ctx.net.blocks:
            raise KeyError(f"single-line working on unknown block section {w.block_id}")
        pair = _pair(ctx, w.block_id)
        for t in ctx.plan.trains:
            for r in t.runs:
                if r.block_id not in ctx.net.blocks or _pair(ctx, r.block_id) != pair:
                    continue
                if overlaps(r.enter_s, r.clear_s, w.start_s, w.end_s, 0.0):
                    out.append((k, t.run_id, ctx.net.blocks[r.block_id].direction, r))
    return out


def _on_working_line(
    ctx: Context, runs: list[tuple[int, str, str, BlockRun]]
) -> list[tuple[int, str, str, BlockRun]]:
    return [(k, t, d, r) for k, t, d, r in runs if r.line_id != ctx.plan.single_line[k].blocked_line_id]


def single_line_working(ctx: Context) -> list[Violation] | None:
    """VER-SLW-01: under single-line working no train uses the blocked line, and trains of opposite
    directions are never on the working line at the same time (no head-on)."""
    if not ctx.plan.single_line:
        return None
    tol = float(ctx.param("ver.time_tolerance_s"))
    runs = _working_runs(ctx)
    out = []
    for k, tid, _, r in runs:
        w = ctx.plan.single_line[k]
        # a train that enters the blocked line during the working (the failed train that blocks it, or one
        # still clearing it when the working began, entered before)
        if r.line_id == w.blocked_line_id and w.start_s + tol <= r.enter_s < w.end_s - tol:
            out.append(
                Violation(
                    trains=[tid],
                    time_s=r.enter_s,
                    detail=f"{tid} on the blocked line {w.block_id}/{w.blocked_line_id} under single-line "
                    "working",
                )
            )
    working = _on_working_line(ctx, runs)
    for i, (k, tid, d, r) in enumerate(working):
        for k2, tid2, d2, r2 in working[i + 1 :]:
            if k != k2 or tid == tid2 or d == d2 or r.line_id != r2.line_id:
                continue  # a 3-line section keeps two working lines: opposing trains on different ones
            if overlaps(r.enter_s, r.clear_s, r2.enter_s, r2.clear_s, tol):
                out.append(
                    Violation(
                        trains=sorted((tid, tid2)),
                        time_s=max(r.enter_s, r2.enter_s),
                        detail=f"{tid} ({d}) and {tid2} ({d2}) both on the working line {r.line_id} of "
                        f"{ctx.plan.single_line[k].block_id}",
                    )
                )
    return out


def single_line_crossing(ctx: Context) -> list[Violation] | None:
    """VER-SLW-02: crossings under single-line working are deadlock-free: an opposing train enters the
    working line only once the previous train has cleared it and the adequate distance (it waits at the
    station and never meets the other train on the single line)."""
    if not ctx.plan.single_line:
        return None
    tol = float(ctx.param("ver.time_tolerance_s"))
    adequate = float(ctx.param("block.adequate_distance_m.macls"))
    speed = float(ctx.param("ver.clearing_speed_kmh")) / KMH_PER_MPS
    length = {t.run_id: t.length_m for t in ctx.plan.trains}
    working = sorted(
        _on_working_line(ctx, _working_runs(ctx)),
        key=lambda x: (x[0], x[-1].line_id, x[-1].enter_s),
    )
    out = []
    for (k, tid, d, r), (k2, tid2, d2, r2) in itertools.pairwise(working):
        if k != k2 or d == d2 or tid == tid2 or r.line_id != r2.line_id:
            continue
        gap = (length[tid] + adequate) / speed
        if r2.enter_s < r.clear_s + gap - tol:
            out.append(
                Violation(
                    trains=[tid, tid2],
                    time_s=r2.enter_s,
                    detail=f"{tid2} ({d2}) enters the working line of {ctx.plan.single_line[k].block_id} "
                    f"{r.clear_s + gap - r2.enter_s:.0f} s before {tid} ({d}) has cleared it plus adequate "
                    "distance",
                )
            )
    return out


def closed_exit(ctx: Context) -> list[Violation] | None:
    """VER-EXT-01: no train reaches a section exit while the exit is closed (boundary acceptance)."""
    if not ctx.plan.closed_exits:
        return None
    tol = float(ctx.param("ver.time_tolerance_s"))
    out = []
    for c in ctx.plan.closed_exits:
        for t in ctx.plan.trains:
            if not t.calls or t.calls[-1].op != c.op or len(t.calls) <= len(t.runs):
                continue  # not a train that left the section at this exit
            arr = t.calls[-1].arr_s
            if c.start_s + tol < arr < c.end_s - tol:
                out.append(
                    Violation(
                        trains=[t.run_id],
                        time_s=arr,
                        detail=f"{t.run_id} reaches the exit {c.op} at {arr:.0f} s while it is closed "
                        f"({c.start_s:.0f}-{c.end_s:.0f} s)",
                    )
                )
    return out
