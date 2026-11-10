# CP-SAT latency spike on the full KTV-PSA section (issue #76)

Spec §12.3 "latency spike (M1)", RK-06, NFR-SCL-03, OD-33; DR-0002 F19. Raw results:
[`cpsat-latency-spike.json`](cpsat-latency-spike.json) (15 s, all sizes) and
[`cpsat-latency-spike-70-60s.json`](cpsat-latency-spike-70-60s.json) (70 trains, 60 s).
Reproduce them with `uv run python tools/cpsat_spike.py` and
`uv run python tools/cpsat_spike.py --sizes 70 --time-limit 60 --out-name cpsat-latency-spike-70-60s`.

## Set-up

- **Instances.** Solver IR (#22, #74) of the published network `KTV-PSA-2025-08` on the real day 2025-08-20,
  as DISPLIB 2025 documents. Each covers the full section, all 22 stations, with every permitted station
  line, block line and sub-block as an alternative. Windows start at the day's 2 h peak (03:30, with 23
  trains entering) and grow until 20, 40 and 70 trains have entered.
- **Model.** `raildss_optimiser.cpsat`: path choice, start times, optional intervals with `NoOverlap` per
  resource (release times included), and the `op_delay` objective (priority-weighted delay, OD-04).
- **Runs.** Each window is solved twice. *Warm* is hinted with the BL-0 twin schedule (FR-OPT-03); *cold*
  has no hint. The limits are `opt.time_limit_s` routine = 15 s (Tier 2 budget 30 s P95, NFR-PERF-02) and
  disruption = 60 s (NFR-PERF-03), with `opt.workers` = 8.
- **Machine.** A shared Windows workstation (AMD64) that was under load from other sessions, with OR-Tools
  9.15. The timings are therefore pessimistic.
- **Check.** Every hint and every CP-SAT solution is re-checked by the independent G1 evaluator (#77).
  **All passed.**

## Results

| Trains | Window | Operations | BL-0 objective | Warm: first feasible | Warm: best in limit | Cold: first feasible | Cold: best in limit |
|---|---|---|---|---|---|---|---|
| 20 | 1.75 h | 2 309 | 75 478 | 1.5 s | **55 283 (-27 %)** at 15 s | 4.8 s | 93 963 (+24 %) at 15 s |
| 40 | 3.9 h | 4 396 | 162 750 | 3.7 s | **150 883 (-7 %)** at 15 s | 8.9 s | 254 290 (+56 %) at 15 s |
| 70 | 7.5 h | 7 786 | 623 310 | none in 15 s (7-9 s in an earlier run) | none in 15 s | none in 15 s | none in 15 s |
| 70 | 7.5 h | 7 786 | 623 310 | 7.7 s | 616 845 (-1 %) at 60 s | 10.6 s | 763 393 (+22 %) at 60 s |

The objective is the priority-weighted delay in seconds over the window. Lower is better; the percentages
are relative to BL-0.

## Reading

- **Real traffic is far below 70 trains per 2 h.** The busiest 2 h of the day has 23 trains entering the
  section. A 120 min rolling horizon (OD-25) therefore holds about 20-40 trains: those entering plus
  those already running. At that size, warm CP-SAT returns a G1-verified plan **better than BL-0 within
  the 15 s routine limit**: -27 % at 20 trains and -7 % at 40.
- **70 trains** (NFR-SCL-03's stress size, here a 7.5 h window) is at the edge. A first feasible plan comes
  in about 8-10 s, inside the Tier 2 budget, but it barely improves on BL-0 in 60 s. On a loaded machine,
  no plan came within 15 s.
- **The warm start matters.** Cold runs are slower to a first plan and stay worse than BL-0. The greedy hint
  and fallback of FR-OPT-03 are needed, not optional.
- **The bound is weak.** The gap stays near 100 %, because the LP bound of this disjunctive model is close to
  0. Quality is therefore judged against BL-0, not by a proved optimum.

## Decision

The decision is recorded in [DR-0004](../decisions/0004-od-33-mvp-extent.md): the **full KTV-PSA section** is
the MVP extent, and the VZM-PSA fallback is not taken.
