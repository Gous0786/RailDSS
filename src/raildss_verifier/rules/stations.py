"""Station rules: crossing facility, CSR, line direction, Connections, line capacity, platform, traction."""

from __future__ import annotations

from collections.abc import Iterator

from raildss_verifier.engine import Context
from raildss_verifier.model import StationCall, TrainPlan, Violation
from raildss_verifier.network import BOTH
from raildss_verifier.rules import call_directions, overlaps


def _calls(ctx: Context) -> Iterator[tuple[TrainPlan, int, StationCall]]:
    for t in ctx.plan.trains:
        for i, c in enumerate(t.calls):
            yield t, i, c


def _uses_single_line(ctx: Context, t: TrainPlan, i: int) -> bool:
    for j in (i - 1, i):
        if 0 <= j < len(t.runs):
            line = ctx.net.lines.get((t.runs[j].block_id, t.runs[j].line_id))
            if line is not None and line.direction == BOTH:
                return True
    return False


def crossing_facility(ctx: Context) -> list[Violation]:
    """VER-XNG-01: a crossing or a precedence (overtake) only at a station with crossing facility."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    at: dict[str, list[tuple[TrainPlan, int, StationCall]]] = {}
    for t, i, c in _calls(ctx):
        at.setdefault(c.op, []).append((t, i, c))
    out = []
    for op, calls in at.items():
        station = ctx.net.stations.get(op)
        if station is None or station.crossing_facility:
            continue
        for k, (t1, i1, c1) in enumerate(calls):
            for t2, i2, c2 in calls[k + 1 :]:
                if t1.run_id == t2.run_id or not overlaps(c1.arr_s, c1.dep_s, c2.arr_s, c2.dep_s, tol):
                    continue
                d1, d2 = call_directions(ctx.net, t1, i1), call_directions(ctx.net, t2, i2)
                if d1 == d2:
                    first, second = (c1, c2) if c1.arr_s <= c2.arr_s else (c2, c1)
                    what = "precedence" if second.dep_s < first.dep_s - tol else ""
                else:
                    single = _uses_single_line(ctx, t1, i1) or _uses_single_line(ctx, t2, i2)
                    what = "crossing" if single else ""
                if what:
                    out.append(
                        Violation(
                            trains=sorted((t1.run_id, t2.run_id)),
                            time_s=max(c1.arr_s, c2.arr_s),
                            detail=f"{what} of {t1.run_id} and {t2.run_id} at {op}, which has no "
                            "crossing facility",
                        )
                    )
    return out


def loop_fits(ctx: Context) -> list[Violation]:
    """VER-XNG-02: a train standing on a station line fits within the line's CSR."""
    out = []
    for t, _, c in _calls(ctx):
        track = ctx.net.tracks.get((c.op, c.line_no))
        if c.stop and track is not None and track.csr_m is not None and track.csr_m < t.length_m:
            out.append(
                Violation(
                    trains=[t.run_id],
                    time_s=c.arr_s,
                    detail=f"{t.run_id} ({t.length_m:.0f} m) stands on {c.op} line {c.line_no} "
                    f"(CSR {track.csr_m:.0f} m)",
                )
            )
    return out


def line_direction(ctx: Context) -> list[Violation]:
    """VER-XNG-03: the station line permits the train's running direction."""
    out = []
    for t, i, c in _calls(ctx):
        track = ctx.net.tracks.get((c.op, c.line_no))
        if track is None or track.directions == BOTH:
            continue
        wrong = call_directions(ctx.net, t, i) - {track.directions}
        if wrong:
            out.append(
                Violation(
                    trains=[t.run_id],
                    time_s=c.arr_s,
                    detail=f"{t.run_id} runs {'/'.join(sorted(wrong))} on {c.op} line {c.line_no}, "
                    f"which permits {track.directions} only",
                )
            )
    return out


def connections(ctx: Context) -> list[Violation]:
    """VER-CON-01: reception and despatch only along Connections edges."""
    out = []
    for t, i, c in _calls(ctx):
        if (c.op, c.line_no) not in ctx.net.tracks:
            out.append(
                Violation(trains=[t.run_id], time_s=c.arr_s, detail=f"{c.op} line {c.line_no} is not in use")
            )
            continue
        moves = []
        if i > 0 and i - 1 < len(t.runs):
            moves.append((t.runs[i - 1], "receive", c.arr_s))
        if i < len(t.runs):
            moves.append((t.runs[i], "send", c.dep_s))
        for run, mode, when in moves:
            if (c.op, c.line_no, run.block_id, run.line_id, mode) not in ctx.net.connections:
                out.append(
                    Violation(
                        trains=[t.run_id],
                        time_s=when,
                        detail=f"no Connections edge: {c.op} line {c.line_no} {mode} "
                        f"{run.block_id}/{run.line_id}",
                    )
                )
    return out


def line_capacity(ctx: Context) -> list[Violation]:
    """VER-CAP-02: at most the line's train capacity on a station line at a time."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    on: dict[tuple[str, int], list[tuple[float, float, str]]] = {}
    for t, _, c in _calls(ctx):
        on.setdefault((c.op, c.line_no), []).append((c.arr_s, c.dep_s, t.run_id))
    out = []
    for (op, no), stays in on.items():
        track = ctx.net.tracks.get((op, no))
        cap = track.capacity if track else 1
        for arr, _, tid in stays:
            present = sorted({x for a, d, x in stays if a <= arr + tol and d > arr + tol} | {tid})
            if len(present) > cap:
                out.append(
                    Violation(
                        trains=present,
                        time_s=arr,
                        detail=f"{len(present)} trains on {op} line {no} (capacity {cap})",
                    )
                )
    return out


def platform_fits(ctx: Context) -> list[Violation]:
    """VER-PLT-01: passenger stops on a platform line whose platform is at least the train's length."""
    out = []
    for t, _, c in _calls(ctx):
        if t.kind != "passenger" or c.booked_dep_s is None:
            continue
        track = ctx.net.tracks.get((c.op, c.line_no))
        if track is None:
            continue
        if not track.platform:
            detail = f"{t.run_id} stops at {c.op} on non-platform line {c.line_no}"
        elif track.platform_length_m is not None and track.platform_length_m < t.length_m:
            detail = (
                f"{t.run_id} ({t.length_m:.0f} m) stops at {c.op} line {c.line_no}, platform "
                f"{track.platform_length_m:.0f} m"
            )
        else:
            continue
        out.append(Violation(trains=[t.run_id], time_s=c.arr_s, detail=detail))
    return out


def electrified(ctx: Context) -> list[Violation]:
    """VER-ELE-01: electric-traction trains only on electrified station lines."""
    out = []
    for t, _, c in _calls(ctx):
        track = ctx.net.tracks.get((c.op, c.line_no))
        if t.electric and track is not None and track.electrified is False:
            out.append(
                Violation(
                    trains=[t.run_id],
                    time_s=c.arr_s,
                    detail=f"electric train {t.run_id} on non-electrified {c.op} line {c.line_no}",
                )
            )
    return out
