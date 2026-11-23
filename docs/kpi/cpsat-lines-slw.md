# CP-SAT on the 3-line block, line blocks and single-line working (issue #153)

Each scenario was run on its own, both loop arms, a 30-minute window: `python -m raildss_orchestrator scenarios <id> --span-min 30`. The window starts 15 min before the first disruption; SCN-19 has none, so it starts at its busiest hour. **BL-1** is the loop with the greedy planner and **DSS** the loop with the CP-SAT rolling horizon. Executed safety is audited on each arm's whole-day ground truth from the window start, against the network as worked (VER-BLK-04 head-on movements, deadlocks, stuck trains). A5 charges a short-terminated or cancelled train the planner's penalty (`opt.disruption.terminate_penalty_min`).

| Scenario | CP-SAT plans verified (DSS) | Executed head-on / deadlocks / stuck (DSS) | A5 DSS | A5 BL-1 | DSS - BL-1 |
|---|---:|---|---:|---:|---:|
| SCN-05 | 12 of 12 | 0 / 0 / 0 | 5573.39 | 5573.39 | +0.00 |
| SCN-19 | 13 of 13 | 0 / 0 / 0 | 7866.56 | 7866.56 | +0.00 |
| SCN-20 | 9 of 10 | 0 / 0 / 0 | 5143.69 | 6085.55 | -941.86 |
| SCN-21 | 12 of 13 | 0 / 0 / 0 | pending | pending | pending |

**SCN-21 is pending.** Its run before the A5 fix verified 12 of 13 CP-SAT plans with 0 head-on movements, deadlocks or stuck trains, and gave DSS = BL-1 on A5 (9812.76); both arms short-terminated two trains. The re-run with the corrected A5 did not finish in time and is left to #170.

DSS weighted delay (A5) is at most greedy's on SCN-05 (equal) and SCN-20 (942 lower). On SCN-05 and SCN-19 the accepted orders change waits inside the section but no train's arrival or exit: the trains still pass the bottleneck in the same order, so the arms tie.

### SCN-05: DN line blocked CPP-SGDM: single-line working on the UP line

- BL-1: window 9.75-10.25 h, 12 plans, **13 of 17 candidate plans passed G0-G3**, 19 recommendations accepted, 18 orders.
  - option recross: 1 of 5 verified, 0 recommendations decided
  - contingent ETR plans: 2 of 2 verified; if not restored by 12:53, switch to the plan for ETR 13:23 (contingent-etr+30, verified); if not restored by 13:23, switch to the plan for ETR 13:53 (contingent-etr+60, verified)
  - disruption first plan: P95 18.4 s over 1 disruption cycles (max 18.4 s; DSC-06 budget 60 s)
  - CP-SAT plans verified: 0 of 0; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains
- DSS: window 9.75-10.25 h, 12 plans, **20 of 29 candidate plans passed G0-G3**, 44 recommendations accepted, 32 orders.
  - option recross: 1 of 5 verified, 0 recommendations decided
  - contingent ETR plans: 2 of 2 verified; if not restored by 12:53, switch to the plan for ETR 13:23 (contingent-etr+30, verified); if not restored by 13:23, switch to the plan for ETR 13:53 (contingent-etr+60, verified)
  - disruption first plan: P95 17.8 s over 1 disruption cycles (max 17.8 s; DSC-06 budget 60 s)
  - CP-SAT plans verified: 12 of 12; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains

| KPI | BL-1 | DSS | BL-0 | BL-1 - BL-0 | DSS - BL-0 | DSS - BL-1 |
|---|---:|---:|---:|---:|---:|---:|
| A2_section_punctuality_pct | 67.53 | 67.53 | 67.53 | +0.00 | +0.00 | +0.00 |
| A4_mean_arrival_delay_min | 8.8 | 8.8 | 8.8 | +0.00 | +0.00 | +0.00 |
| A5_weighted_delay_train_min | 5573.39 | 5573.39 | 5573.39 | +0.00 | +0.00 | +0.00 |
| A6_mean_exit_delay_min | 11.57 | 11.57 | 11.57 | +0.00 | +0.00 | +0.00 |
| A6_p90_exit_delay_min | 49.88 | 49.88 | 49.88 | +0.00 | +0.00 | +0.00 |
| B1_trains_exited | 172 | 172 | 172 | +0.00 | +0.00 | +0.00 |
| C2_freight_detention_h | 5.16 | 5.16 | 5.16 | +0.00 | +0.00 | +0.00 |
| F1_unplanned_stops | 458 | 458 | 466 | -8.00 | -8.00 | +0.00 |

Conflicts (D1, D2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 41 (0 near-conflicts) | |
| D1 median conflict lead time | 43.4 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 53.33 % (8 resolved, 7 occurred, 26 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 41 (41 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 82 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 1 (most alerts in one window: 28) | 0 (DSC-09) |

Conflicts (D1, D2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 41 (0 near-conflicts) | |
| D1 median conflict lead time | 43.4 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 53.33 % (8 resolved, 7 occurred, 26 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 41 (41 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 82 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 1 (most alerts in one window: 28) | 0 (DSC-09) |
### SCN-19: Peak KTV-VZM on the 3-line automatic block: precedence, overtakes and line choice

- BL-1: window 5.00-5.50 h, 13 plans, **13 of 13 candidate plans passed G0-G3**, 14 recommendations accepted, 14 orders.
  - CP-SAT plans verified: 0 of 0; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains
- DSS: window 5.00-5.50 h, 13 plans, **15 of 26 candidate plans passed G0-G3**, 16 recommendations accepted, 16 orders.
  - CP-SAT plans verified: 13 of 13; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains

| KPI | BL-1 | DSS | BL-0 | BL-1 - BL-0 | DSS - BL-0 | DSS - BL-1 |
|---|---:|---:|---:|---:|---:|---:|
| A2_section_punctuality_pct | 69.47 | 69.47 | 69.47 | +0.00 | +0.00 | +0.00 |
| A4_mean_arrival_delay_min | 11.86 | 11.86 | 11.86 | +0.00 | +0.00 | +0.00 |
| A5_weighted_delay_train_min | 7866.56 | 7866.56 | 7866.56 | +0.00 | +0.00 | +0.00 |
| A6_mean_exit_delay_min | 13.4 | 13.4 | 13.4 | +0.00 | +0.00 | +0.00 |
| A6_p90_exit_delay_min | 48.39 | 48.39 | 48.39 | +0.00 | +0.00 | +0.00 |
| B1_trains_exited | 191 | 191 | 191 | +0.00 | +0.00 | +0.00 |
| C2_freight_detention_h | 4.83 | 4.83 | 4.83 | +0.00 | +0.00 | +0.00 |
| F1_unplanned_stops | 525 | 527 | 536 | -11.00 | -9.00 | +2.00 |

Conflicts (D1, D2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 46 (0 near-conflicts) | |
| D1 median conflict lead time | 45.7 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 55 % (11 resolved, 9 occurred, 26 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 46 (46 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 92 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 3 (most alerts in one window: 22) | 0 (DSC-09) |

Conflicts (D1, D2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 46 (0 near-conflicts) | |
| D1 median conflict lead time | 45.7 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 55 % (11 resolved, 9 occurred, 26 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 46 (46 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 92 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 3 (most alerts in one window: 22) | 0 (DSC-09) |
### SCN-20: Planned line block on DUSI-CHE (maintenance corridor) with overrun

- BL-1: window 10.75-11.25 h, 14 plans, **16 of 21 candidate plans passed G0-G3**, 29 recommendations accepted, 31 orders.
  - option recross: 0 of 5 verified, 0 recommendations decided
  - option short-term: 2 of 2 verified, 11 recommendations decided
  - contingent ETR plans: 2 of 2 verified; if not restored by 12:34, switch to the plan for ETR 13:04 (contingent-etr+30, verified); if not restored by 13:04, switch to the plan for ETR 13:34 (contingent-etr+60, verified)
  - disruption first plan: P95 23.0 s over 1 disruption cycles (max 23.0 s; DSC-06 budget 60 s)
  - CP-SAT plans verified: 0 of 0; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains
- DSS: window 10.75-11.25 h, 13 plans, **11 of 30 candidate plans passed G0-G3**, 18 recommendations accepted, 16 orders.
  - option short-term: 0 of 7 verified, 0 recommendations decided
  - contingent ETR plans: 0 of 2 verified; if not restored by 12:34, switch to the plan for ETR 13:04 (contingent-etr+30, not verified); if not restored by 13:04, switch to the plan for ETR 13:34 (contingent-etr+60, not verified)
  - disruption first plan: P95 180.8 s over 1 disruption cycles (max 180.8 s; DSC-06 budget 60 s)
  - CP-SAT plans verified: 9 of 10; executed from the window start: 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains

| KPI | BL-1 | DSS | BL-0 | BL-1 - BL-0 | DSS - BL-0 | DSS - BL-1 |
|---|---:|---:|---:|---:|---:|---:|
| A2_section_punctuality_pct | 81.82 | 79.75 | 79.75 | +2.07 | +0.00 | -2.07 |
| A4_mean_arrival_delay_min | 8.48 | 8.94 | 9 | -0.52 | -0.06 | +0.46 |
| A5_weighted_delay_train_min | 6085.55 | 5143.69 | 5153.48 | +932.07 | -9.79 | -941.86 |
| A6_mean_exit_delay_min | 8.47 | 9.91 | 9.91 | -1.44 | +0.00 | +1.44 |
| A6_p90_exit_delay_min | 38.9 | 40.26 | 40.26 | -1.36 | +0.00 | +1.36 |
| B1_trains_exited | 173 | 175 | 175 | -2.00 | +0.00 | +2.00 |
| C2_freight_detention_h | 4.36 | 4.47 | 4.61 | -0.25 | -0.14 | +0.11 |
| F1_unplanned_stops | 364 | 380 | 389 | -25.00 | -9.00 | +16.00 |

Conflicts (D1, D2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 79 (0 near-conflicts) | |
| D1 median conflict lead time | 31 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 46 % (23 resolved, 27 occurred, 29 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 79 (79 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 158 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 4 (most alerts in one window: 48) | 0 (DSC-09) |

Conflicts (D1, D2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 79 (0 near-conflicts) | |
| D1 median conflict lead time | 31 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 40 % (20 resolved, 30 occurred, 29 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 79 (79 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 158 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 4 (most alerts in one window: 48) | 0 (DSC-09) |
