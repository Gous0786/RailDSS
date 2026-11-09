"""Plan-level rules: early departure, competent-authority actions, frozen decisions."""

from __future__ import annotations

from raildss_verifier.engine import Context
from raildss_verifier.model import Violation


def early_departure(ctx: Context) -> list[Violation]:
    """VER-PLN-01: no passenger departure before the booked time."""
    tol = float(ctx.param("ver.time_tolerance_s"))
    return [
        Violation(
            trains=[t.run_id],
            time_s=c.dep_s,
            detail=f"{t.run_id} departs {c.op} {c.booked_dep_s - c.dep_s:.0f} s before its booked time",
        )
        for t in ctx.plan.trains
        if t.kind == "passenger"
        for c in t.calls
        if c.booked_dep_s is not None and c.dep_s < c.booked_dep_s - tol
    ]


def authority(ctx: Context) -> list[Violation]:
    """VER-AUT-01 (FR-VER-10): no action that only a competent authority may authorise."""
    kinds = set(ctx.param("ver.authority_actions"))
    return [
        Violation(
            trains=[a.run_id],
            time_s=a.time_s,
            detail=f"action {a.kind!r} needs competent-authority authorisation and is never generated",
        )
        for a in ctx.plan.actions
        if a.kind in kinds
    ]


def frozen(ctx: Context) -> list[Violation] | None:
    """VER-FRZ-01: frozen decisions (commit window, line clear granted) are unchanged."""
    if not ctx.plan.frozen:
        return None
    tol = float(ctx.param("ver.time_tolerance_s"))
    trains = {t.run_id: t for t in ctx.plan.trains}
    out = []
    for f in ctx.plan.frozen:
        t = trains.get(f.run_id)
        calls = [c for c in t.calls if c.op == f.op] if t else []
        if not any(c.line_no == f.line_no and abs(c.dep_s - f.dep_s) <= tol for c in calls):
            got = ", ".join(f"line {c.line_no} dep {c.dep_s:.0f} s" for c in calls) or "no call"
            out.append(
                Violation(
                    trains=[f.run_id],
                    time_s=f.dep_s,
                    detail=f"frozen {f.run_id} at {f.op} line {f.line_no} dep {f.dep_s:.0f} s; "
                    f"plan has {got}",
                )
            )
    return out
