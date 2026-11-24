# Freight detention and baseline realism (issue #145; DSC-20, BL-5, FV-02)

The twin detains a freight train at intermediate block stations from the log's K1 dwell statistics (`twin.freight_detention`, `src/raildss_twin/detention.py`): with the station's share of calls over 30 min it halts 30 min plus an exponential tail fitted to the station's P90, times `scale` (1.4), on a free loop only. BL-0 and every arm, the planner's twin runs and the CP-SAT IR see the same seeded halts. `enabled: false` in the register runs without them.

## DSC-20: freight through-transit on the 27 real days

BL-0 on each real day of Aug 2025 (default seed). Through-transit as BL-5 defines it: loads whose path calls at 15 or more section stations, section entry to exit. BL-1 runs the decision loop only in a window, so its day-level freight transit is the same as BL-0's to within the window's effect.

| | Twin, BL-0 | BL-5 (history) | Gap |
|---|---:|---:|---:|
| Median through-transit (h), 15+ stations | 8.02 | 10.41 | -23 % |
| Median section time (h), every freight load | 2.93 | | |

**Within ±25 % of BL-5 (DSC-20).** Every day ran to completion with no stuck train and no deadlock. The remaining gap is a twin-realism gap (RK-23): the log includes causes the twin does not model (loco and crew availability, yard and branch interactions, failures), and the halts are not tuned to close it.

| Day | Median, every load (h) | Loads | Median, 15+ stations (h) | Loads | Stuck | Deadlocks |
|---|---:|---:|---:|---:|---:|---:|
| 2025-08-01 | 2.48 | 110 | 8.15 | 31 | 0 | 0 |
| 2025-08-02 | 3.06 | 102 | 7.61 | 35 | 0 | 0 |
| 2025-08-03 | 2.38 | 98 | 8.47 | 33 | 0 | 0 |
| 2025-08-04 | 3.76 | 91 | 7.67 | 30 | 0 | 0 |
| 2025-08-05 | 2.92 | 96 | 7.33 | 37 | 0 | 0 |
| 2025-08-06 | 2.95 | 89 | 8.39 | 33 | 0 | 0 |
| 2025-08-07 | 2.92 | 95 | 7.17 | 27 | 0 | 0 |
| 2025-08-08 | 2.96 | 91 | 9.25 | 26 | 0 | 0 |
| 2025-08-09 | 2.6 | 96 | 8.0 | 30 | 0 | 0 |
| 2025-08-10 | 3.28 | 103 | 8.63 | 35 | 0 | 0 |
| 2025-08-11 | 2.71 | 94 | 8.15 | 30 | 0 | 0 |
| 2025-08-12 | 2.22 | 106 | 6.81 | 18 | 0 | 0 |
| 2025-08-13 | 3.18 | 90 | 7.32 | 30 | 0 | 0 |
| 2025-08-14 | 3.06 | 99 | 7.31 | 36 | 0 | 0 |
| 2025-08-15 | 3.32 | 96 | 8.22 | 38 | 0 | 0 |
| 2025-08-16 | 1.85 | 103 | 8.79 | 37 | 0 | 0 |
| 2025-08-17 | 2.91 | 97 | 8.97 | 34 | 0 | 0 |
| 2025-08-18 | 3.22 | 106 | 7.33 | 42 | 0 | 0 |
| 2025-08-19 | 3.26 | 91 | 7.57 | 35 | 0 | 0 |
| 2025-08-20 | 4.53 | 94 | 8.39 | 42 | 0 | 0 |
| 2025-08-21 | 2.95 | 89 | 8.38 | 31 | 0 | 0 |
| 2025-08-22 | 3.81 | 100 | 8.2 | 41 | 0 | 0 |
| 2025-08-23 | 2.8 | 100 | 6.84 | 34 | 0 | 0 |
| 2025-08-24 | 4.0 | 91 | 8.33 | 34 | 0 | 0 |
| 2025-08-25 | 3.44 | 94 | 7.67 | 35 | 0 | 0 |
| 2025-08-26 | 2.63 | 89 | 8.02 | 33 | 0 | 0 |
| 2025-08-27 | 2.75 | 94 | 9.48 | 22 | 0 | 0 |

## VZM dwell (and four more stations)

See `bl0-reference-check-2025-08-20.md`: BL-0 on 2025-08-20 against the log's P90 and share of stops over 30 min (VZM: history P90 95 min, 29.7 %).

Generated from `python -m raildss_twin run` days (BL-0) with the halts of `twin.freight_detention`; the script is the issue's evidence run.
