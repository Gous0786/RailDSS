"""Command line for the optimiser.

python -m raildss_optimiser greedy --date 2025-08-20 --at-h 8                 # greedy plan from 08:00
python -m raildss_optimiser greedy --at-h 8 --close DUSI~PDU/UP@8.5-9.5       # single-line working
python -m raildss_optimiser greedy --at-h 8 --exit-closed PSA@8.5-9           # closed boundary exit
python -m raildss_optimiser greedy --at-h 8 --conflicts      # DS-5 conflicts and recommendations

Times are hours of the day. ``--close SEGMENT/LINE@FROM-TO`` (LINE ``*`` = every line) and
``--exit-closed STATION@FROM-TO`` may be repeated.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from raildss_twin.sim import Closure

SECONDS_PER_HOUR = 3600


def _window(text: str) -> tuple[str, float, float]:
    what, _, span = text.partition("@")
    a, _, b = span.partition("-")
    return what, float(a) * SECONDS_PER_HOUR, float(b) * SECONDS_PER_HOUR


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_optimiser")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("greedy", help="greedy priority dispatcher from a time of a twin day (issue #75)")
    g.add_argument("--date", default="2025-08-20")
    g.add_argument("--at-h", type=float, required=True, help="plan from this hour of the day")
    g.add_argument("--close", action="append", default=[], help="SEGMENT/LINE@FROM-TO (hours)")
    g.add_argument("--exit-closed", action="append", default=[], help="STATION@FROM-TO (hours)")
    g.add_argument(
        "--conflicts", action="store_true", help="run the DSS chain and recommend for its conflicts"
    )
    args = ap.parse_args(argv)

    from raildss_optimiser.greedy import GreedyDispatcher
    from raildss_twin import run

    closures = []
    for c in args.close:
        where, a, b = _window(c)
        seg, _, line = where.partition("/")
        closures.append(Closure(seg, None if line in ("", "*") else line, a, b))
    exits: dict[str, list[tuple[float, float]]] = {}
    for e in args.exit_closed:
        op, a, b = _window(e)
        exits.setdefault(op, []).append((a, b))
    world = run.load_world()
    day = dt.date.fromisoformat(args.date)
    at_s = args.at_h * SECONDS_PER_HOUR
    if args.conflicts:
        from raildss_optimiser.pipeline import recommend_at

        out = recommend_at(world, day, at_s, closures=closures, exit_windows=exits)
        plan = out.plan
        print(f"{out.at.isoformat()}: {len(out.conflicts)} conflicts detected")
        for r in out.recommendations:
            print(f"  type {r.conflict_type} on {r.resource} ({', '.join(r.trains)}): {r.status}")
            for action in r.actions:
                print(f"    - {action}")
    else:
        plan = GreedyDispatcher(world).plan(day, at_s, closures=closures, exit_windows=exits)
    print(
        f"greedy plan {plan.plan_id}: G2 {plan.g2.overall.value} ({plan.g2_fails} FAIL), "
        f"{len(plan.decisions)} departures, {len(plan.holds)} holds, stuck {len(plan.stuck)}, "
        f"{plan.runtime_s:.1f} s"
    )
    for h in sorted(plan.holds, key=lambda h: -h.held_s)[:10]:
        print(f"  hold {h.run_id} at {h.station}: {h.held_s / 60:.0f} min")
    return 0 if plan.ok else 1


if __name__ == "__main__":
    sys.exit(main())
