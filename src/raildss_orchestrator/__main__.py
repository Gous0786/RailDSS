"""Command line for the decision orchestrator.

python -m raildss_orchestrator run --date 2025-08-20 --from-h 8 --to-h 9      # one twin day window, headless
python -m raildss_orchestrator run --from-h 8 --to-h 10 --out run.json --kpis  # the log and the day's KPIs
python -m raildss_orchestrator run --from-h 8 --to-h 9 --close DUSI~PDU/UP@8.5-9.5 --notice-min 20

The run replays the twin day under a degradation profile, feeds it to the DSS chain tick by tick on a
simulated clock, re-plans per OD-25 (on triggers and at least every ``opt.replan_max_interval_min``),
verifies every option (G0-G3), releases at most three per decision plus the baseline, and, headless,
accepts the best option and issues its orders. Times are hours of the day.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from raildss_orchestrator.loop import Disruption, Orchestrator
from raildss_orchestrator.twin import TwinDss, TwinPlanner, day_kpis
from raildss_twin import run
from raildss_twin.sim import Closure

SECONDS_PER_HOUR = 3600
SECONDS_PER_MIN = 60
NEWLINE = "\n"


def _window(text: str) -> tuple[str, float, float]:
    what, _, span = text.partition("@")
    a, _, b = span.partition("-")
    return what, float(a) * SECONDS_PER_HOUR, float(b) * SECONDS_PER_HOUR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_orchestrator")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="the rolling loop on a real twin day, headless (issue #84)")
    r.add_argument("--date", default="2025-08-20")
    r.add_argument("--from-h", type=float, required=True)
    r.add_argument("--to-h", type=float, required=True)
    r.add_argument("--tick-s", type=float, default=60.0, help="simulated clock step")
    r.add_argument("--profile", default="D0", help="degradation profile of the observed feeds")
    r.add_argument(
        "--close", action="append", default=[], help="SEGMENT/LINE@FROM-TO (hours), announced early"
    )
    r.add_argument("--exit-closed", action="append", default=[], help="STATION@FROM-TO (hours)")
    r.add_argument(
        "--notice-min", type=float, default=30.0, help="how long before it starts a closure is known"
    )
    r.add_argument("--no-cpsat", action="store_true", help="greedy options only")
    r.add_argument("--alternatives", type=int, default=1, help="extra CP-SAT solutions (other seeds)")
    r.add_argument("--no-auto-accept", action="store_true", help="release only: nobody accepts")
    r.add_argument("--kpis", action="store_true", help="the day's KPIs, DSS arm against BL-0 (two day runs)")
    r.add_argument("--out", type=Path, default=None, help="write the run summary as JSON")
    args = ap.parse_args(argv)

    world = run.load_world()
    day = dt.date.fromisoformat(args.date)
    from_s, to_s = args.from_h * SECONDS_PER_HOUR, args.to_h * SECONDS_PER_HOUR
    closures, disruptions = [], []
    for c in args.close:
        where, a, b = _window(c)
        seg, _, line = where.partition("/")
        closure = Closure(seg, None if line in ("", "*") else line, a, b)
        closures.append(closure)
        disruptions.append(Disruption(closure, a - args.notice_min * SECONDS_PER_MIN))
    exits: dict[str, list[tuple[float, float]]] = {}
    for e in args.exit_closed:
        op, a, b = _window(e)
        exits.setdefault(op, []).append((a, b))
    dss = TwinDss(world, day, profile=args.profile, until_s=to_s, closures=closures, exit_windows=exits)
    planner = TwinPlanner(world, day, cpsat_enabled=not args.no_cpsat, alternatives=args.alternatives)
    loop = Orchestrator(
        world.register,
        dss,
        planner,
        disruptions=tuple(disruptions),
        exit_windows=exits,
        auto_accept=not args.no_auto_accept,
    )
    summary = loop.run(from_s, to_s, args.tick_s)
    for c in summary.cycles:
        best = c.released[0].actions if c.released else []
        print(
            f"cycle {c.cycle} at {c.now_s / SECONDS_PER_HOUR:.2f} h {','.join(c.triggers) or 'periodic'}: "
            f"{c.conflicts} conflicts, {len(c.candidates)} candidates "
            f"({', '.join(f'{x.name}:{x.g3}' for x in c.candidates)}), released {len(c.released)} "
            f"[{c.wall_s:.0f} s]" + (f"; first: {best[0]}" if best else "")
        )
    print(
        f"{summary.ticks} ticks, {len(summary.cycles)} plans "
        f"(longest gap {summary.max_replan_gap_s / SECONDS_PER_MIN:.0f} min), "
        f"{summary.released} recommendations released, {summary.accepted} accepted, {summary.orders} orders, "
        f"{summary.declined} declined, statuses {summary.statuses}"
    )
    doc = summary.model_dump(mode="json")
    if args.kpis:
        holds, lines = loop.commitments()
        doc["kpis"] = day_kpis(world, day, closures=closures, exit_windows=exits, holds=holds, lines=lines)
        print(json.dumps(doc["kpis"]["deltas"], indent=1))
    if args.out is not None:
        args.out.write_text(json.dumps(doc, indent=1) + NEWLINE, encoding="utf-8", newline=NEWLINE)
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
