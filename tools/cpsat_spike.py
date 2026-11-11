"""CP-SAT latency spike on the full KTV-PSA section (issue #76, DR-0002 F19, OD-33, NFR-SCL-03).

Builds solver IR windows of growing size (20, 40, 70 entering trains, starting at the day's peak) on the
real day 2025-08-20, solves each with CP-SAT within the routine time limit (``opt.time_limit_s``), warm
(hinted with the BL-0 twin schedule, FR-OPT-03) and cold, checks every solution with the independent
G1 evaluator, and writes ``docs/optimiser/cpsat-latency-spike.{md,json}``.

Usage: ``uv run python tools/cpsat_spike.py [--sizes 20 40 70] [--time-limit S] [--workers N]``.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import platform
import sys
import time
from pathlib import Path
from typing import Any

from raildss_optimiser.cpsat import order_events, solve
from raildss_twin import displib, run
from raildss_twin.displib_check import schedule_from_trace
from raildss_verifier.gates import g1

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "optimiser"
DAY = dt.date(2025, 8, 20)
HORIZON_S = 2 * 3600


def peak_start(entries: list[float]) -> float:
    """Start of the 2 h window with the most entering trains (15 min steps)."""
    best, best_n = 0.0, -1
    for t in range(0, 24 * 3600 - HORIZON_S, 900):
        n = sum(1 for e in entries if t <= e < t + HORIZON_S)
        if n > best_n:
            best, best_n = float(t), n
    return best


def bl0_solution(instance: displib.Instance, doc: dict[str, Any], log: Any) -> dict[str, Any]:
    """The BL-0 twin schedule as a DISPLIB solution (times floored, like the IR's durations)."""
    schedule = schedule_from_trace(instance, log)
    paths: dict[int, list[tuple[int, int]]] = {}
    for ti, train in enumerate(instance.trains):
        preds = {e.operation for o in train.operations for e in o.successors}
        shift = int(len([o for o in train.operations if o.id not in preds]) > 1)
        ops = {o.id: o for o in train.operations}
        path = [(oid, math.floor(t + 1e-6)) for oid, t in schedule[ti]]  # float noise below a second
        p = [(0, path[0][1])] if shift else []
        p += [(shift + oid, t) for oid, t in path]
        last_oid, last_t = path[-1]
        p.append((shift + len(train.operations), last_t + ops[last_oid].min_duration))
        paths[ti] = p
    events = order_events(doc, paths)
    from raildss_optimiser.cpsat import objective_of

    return {"objective_value": objective_of(doc, paths), "events": events}


def at(trajectory: list[tuple[float, float]], t: float) -> float | None:
    vals = [v for s, v in trajectory if s <= t]
    return vals[-1] if vals else None


def main(argv: list[str] | None = None) -> int:
    world = run.load_world()
    reg = world.register
    ap = argparse.ArgumentParser()
    ap.add_argument("--sizes", type=int, nargs="+", default=[20, 40, 70])
    ap.add_argument("--time-limit", type=float, default=float(reg.value("opt.time_limit_s")["routine"]))
    ap.add_argument("--workers", type=int, default=int(reg.value("opt.workers")))
    ap.add_argument("--out-name", default="cpsat-latency-spike")
    args = ap.parse_args(argv)
    plans, _, _ = run.plans_for(world, DAY)
    entries = sorted(p.entry_s for p in plans)
    start = peak_start(entries)
    peak_n = sum(1 for e in entries if start <= e < start + HORIZON_S)
    rows = []
    for n in args.sizes:
        later = [e for e in entries if e >= start]
        end = later[min(n, len(later)) - 1] + 1
        t0 = time.perf_counter()
        instance, window_plans = displib.build_instance(world, DAY, start, end)
        doc = instance.to_displib()
        build_s = time.perf_counter() - t0
        _, sim = run.run_day(world, DAY, plans=window_plans)
        hint = bl0_solution(instance, doc, sim.occupancy_log)
        hint_g1 = g1(doc, hint)
        row: dict[str, Any] = {
            "trains": len(instance.trains),
            "window": f"{dt.timedelta(seconds=int(start))}-{dt.timedelta(seconds=int(end))}",
            "window_h": round((end - start) / 3600, 2),
            "build_s": round(build_s, 2),
            "bl0_objective": hint["objective_value"],
            "bl0_g1": hint_g1.status,
        }
        for mode in ("warm", "cold"):
            r = solve(
                doc, time_limit_s=args.time_limit, workers=args.workers, hint=hint if mode == "warm" else None
            )
            check = g1(doc, r.solution) if r.solution else None
            gap = (
                round(100 * (r.objective - r.bound) / r.objective, 1)
                if r.objective and r.bound is not None and r.objective > 0
                else None
            )
            row |= {
                "size": r.size,
                f"{mode}_status": r.status,
                f"{mode}_first_feasible_s": r.first_feasible_s,
                f"{mode}_objective_5s": at(r.trajectory, 5.0),
                f"{mode}_objective": r.objective,
                f"{mode}_gap_pct": gap,
                f"{mode}_wall_s": r.wall_s,
                f"{mode}_g1": check.status if check else None,
            }
        rows.append(row)
        print(json.dumps(row), flush=True)
    meta = {
        "day": DAY.isoformat(),
        "network": world.layout.network_version,
        "peak_2h_start": str(dt.timedelta(seconds=int(start))),
        "peak_2h_entering_trains": peak_n,
        "time_limit_s": args.time_limit,
        "workers": args.workers,
        "machine": f"{platform.system()} {platform.machine()}, Python {platform.python_version()}",
        "ortools": __import__("ortools").__version__,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{args.out_name}.json").write_text(
        json.dumps({"meta": meta, "rows": rows}, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(meta))
    return 0


if __name__ == "__main__":
    sys.exit(main())
