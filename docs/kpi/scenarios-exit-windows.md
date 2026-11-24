# Boundary acceptance windows: SCN-23 under the loop (issue #157, FR-OPT-20)

SCN-23 shuts the KTV-VSKP boundary (the exit at KTV) from 14:00 for a seeded 90-120 min. Each arm was run on its own over a 20-minute window from 13:45: `python -m raildss_orchestrator scenarios SCN-23 --arm <arm> --span-min 20`. Executed safety is audited on each arm's whole-day ground truth, with the closed exit as plan context.

| | DSS (CP-SAT) | BL-1 (greedy) |
|---|---:|---:|
| Trains into the closed exit (VER-EXT-01, executed) | 0 | 0 |
| Head-on movements / deadlocks / stuck trains | 0 / 0 / 0 | 0 / 0 / 0 |
| Candidate plans verified / built | 16 / 28 | 10 / 16 |
| CP-SAT plans verified | 5 / 11 | - |

| KPI (day) | DSS | BL-1 | BL-0 | DSS - BL-1 |
|---|---:|---:|---:|---:|
| A5_weighted_delay_train_min | 26427.1 | 26125.5 | 26562.4 | +301.60 |
| A2_section_punctuality_pct | 74.26 | 75.25 | 74.26 | -0.99 |
| A6_mean_exit_delay_min | 13.28 | 12.82 | 13.56 | +0.46 |
| B1_trains_exited | 192 | 192 | 192 | +0.00 |
| C2_freight_detention_h | 303.09 | 302.04 | 303.03 | +1.05 |

**0 trains enter a closed exit in either arm.** Both arms improve on BL-0. In this window the greedy plan (BL-1) does better on A5 than the CP-SAT rolling horizon (DSS); 6 of the 11 CP-SAT plans failed verification, so the DSS arm fell back to greedy options in those cycles.

### SCN-23: Junction exit blocked: boundary closed towards Visakhapatnam at KTV

- BL-1: window 13.75-14.08 h, 11 plans, **10 of 16 candidate plans passed G0-G3**, 10 recommendations accepted, 14 orders.
  - CP-SAT plans verified: 0 of 0; executed from the window start: 0 trains into a closed exit (VER-EXT-01), 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains

| KPI | BL-1 | BL-0 | BL-1 - BL-0 |
|---|---:|---:|---:|
| A2_section_punctuality_pct | 75.25 | 74.26 | +0.99 |
| A4_mean_arrival_delay_min | 12.25 | 12.61 | -0.36 |
| A5_weighted_delay_train_min | 26125.5 | 26562.4 | -436.93 |
| A6_mean_exit_delay_min | 12.82 | 13.56 | -0.74 |
| A6_p90_exit_delay_min | 53.99 | 60.54 | -6.55 |
| B1_trains_exited | 192 | 192 | +0.00 |
| C2_freight_detention_h | 302.04 | 303.03 | -0.99 |
| F1_unplanned_stops | 528 | 573 | -45.00 |

Conflicts (D1, D2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 102 (0 near-conflicts) | |
| D1 median conflict lead time | 33.6 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 51.92 % (27 resolved, 25 occurred, 50 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), BL-1:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 102 (102 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 306 (0.33 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 1 (most alerts in one window: 65) | 0 (DSC-09) |

### SCN-23: Junction exit blocked: boundary closed towards Visakhapatnam at KTV

- DSS: window 13.75-14.08 h, 11 plans, **16 of 28 candidate plans passed G0-G3**, 20 recommendations accepted, 23 orders.
  - CP-SAT plans verified: 5 of 11; executed from the window start: 0 trains into a closed exit (VER-EXT-01), 0 head-on movements (VER-BLK-04), 0 deadlocks, 0 stuck trains

| KPI | DSS | BL-0 | DSS - BL-0 |
|---|---:|---:|---:|
| A2_section_punctuality_pct | 74.26 | 74.26 | +0.00 |
| A4_mean_arrival_delay_min | 12.59 | 12.61 | -0.02 |
| A5_weighted_delay_train_min | 26427.1 | 26562.4 | -135.32 |
| A6_mean_exit_delay_min | 13.28 | 13.56 | -0.28 |
| A6_p90_exit_delay_min | 60.54 | 60.54 | +0.00 |
| B1_trains_exited | 192 | 192 | +0.00 |
| C2_freight_detention_h | 303.09 | 303.03 | +0.06 |
| F1_unplanned_stops | 536 | 573 | -37.00 |

Conflicts (D1, D2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 102 (0 near-conflicts) | |
| D1 median conflict lead time | 33.6 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 51.92 % (27 resolved, 25 occurred, 50 still open) | >= 90 % (DSC-04) |

Alert load (H1, H2), DSS:

| KPI | Value | Target |
|---|---:|---|
| Alerts | 102 (102 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 306 (0.33 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 1 (most alerts in one window: 65) | 0 (DSC-09) |
