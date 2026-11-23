# Closed-loop replay of 2025-08-20

Window 8-8.5 h, options by greedy, evaluation auto-accept, identical seed and demand under BL-0 and under the DSS (FR-SCN-05, FR-SCN-08).

## The loop

| What | Value |
|---|---|
| Clock ticks | 31 |
| Plans (cycles) | 12 |
| Longest gap between plans | 5 min |
| Candidate plans verified (G0-G3) / built | 8 / 18 |
| Recommendations released | 22 |
| Accepted (evaluation policy) | 19 |
| Control orders issued | 13 (0 declined) |
| Most options for one decision | 2 |
| Final statuses | DIVERGED 9, EXPIRED 3, SUPERSEDED 10 |
| Decisions released / with 2+ options / with 2+ precedences | 19 / 3 / 1 |
| Other precedence re-solved / verified / reversed | 9 / 4 / 3 |
| Decisions with 2+ feasible precedences given options of both | 1 / 5 |

## KPIs: DSS against BL-0

### day 2025-08-20

| KPI | DSS | BL-0 |
|---|---|---|
| A2_section_punctuality_pct | 62.63 | 62.63 |
| A2_lop_count | 37 | 37 |
| A4_mean_arrival_delay_min | 20.29 | 20.42 |
| A4_max_arrival_delay_min | 182.01 | 182.01 |
| A5_weighted_delay_train_min | 13814.4 | 14071.5 |
| A6_mean_exit_delay_min | 23.72 | 24.38 |
| A6_median_exit_delay_min | 4.17 | 4.17 |
| A6_p90_exit_delay_min | 83.36 | 83.36 |
| A9_mean_time_lost_min | 25.42 | 26.08 |
| B1_trains_exited | 193 | 193 |
| C1_freight_speed_kmh | 43.39 | 43.65 |
| C2_freight_detention_h | 19.5 | 18.27 |
| F1_unplanned_stops | 653 | 662 |


## Conflicts: lead time and resolution (D1, D2)

The DS-5 conflict log over the window against the ground truth of the DSS arm: a conflict is resolved if it is no longer predicted before its time and no train of it was held at its resource in the ground truth; still-open conflicts are left out of D2.

| KPI | Value | Target |
|---|---:|---|
| Conflicts detected | 36 (0 near-conflicts) | |
| D1 median conflict lead time | 20.5 min | >= 15 min (DSC-05) |
| D2 resolved before occurrence | 16 % (4 resolved, 21 occurred, 11 still open) | >= 90 % (DSC-04) |

## Alert load (H1, H2)

| KPI | Value | Target |
|---|---:|---|
| Alerts | 36 (36 conflicts, 0 data quality) | |
| H1 alerts per controller-hour | 72 (0.5 h, 1 controller) | <= 6 (DSC-09) |
| H2 flood events | 2 (most alerts in one window: 21) | 0 (DSC-09) |
### DSS minus BL-0

| KPI | Delta |
|---|---|
| A2_section_punctuality_pct | +0 |
| A2_lop_count | +0 |
| A4_mean_arrival_delay_min | -0.13 |
| A4_max_arrival_delay_min | +0 |
| A5_weighted_delay_train_min | -257.04 |
| A6_mean_exit_delay_min | -0.66 |
| A6_median_exit_delay_min | +0 |
| A6_p90_exit_delay_min | +0 |
| A9_mean_time_lost_min | -0.66 |
| B1_trains_exited | +0 |
| C1_freight_speed_kmh | -0.26 |
| C2_freight_detention_h | +1.23 |
| F1_unplanned_stops | -9 |

Most affected trains (exit delay, min): KSLKJEA_19082025115439 +109.72, 17480@2025-08-19 -18.06, 22604@2025-08-19 -18.06
