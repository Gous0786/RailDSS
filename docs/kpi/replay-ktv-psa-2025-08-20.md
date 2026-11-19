# Closed-loop replay of 2025-08-20

Window 8-10 h, options by greedy, evaluation auto-accept, identical seed and demand under BL-0 and under the DSS (FR-SCN-05, FR-SCN-08).

## The loop

| What | Value |
|---|---|
| Clock ticks | 61 |
| Plans (cycles) | 51 |
| Longest gap between plans | 6 min |
| Candidate plans verified (G0-G3) / built | 47 / 51 |
| Recommendations released | 35 |
| Accepted (evaluation policy) | 35 |
| Control orders issued | 37 (0 declined) |
| Most options for one decision | 1 |
| Final statuses | DIVERGED 26, SUPERSEDED 9 |

## KPIs: DSS against BL-0

### day 2025-08-20

| KPI | DSS | BL-0 |
|---|---|---|
| A2_section_punctuality_pct | 64.65 | 63.64 |
| A2_lop_count | 35 | 36 |
| A4_mean_arrival_delay_min | 17.45 | 18.92 |
| A4_max_arrival_delay_min | 173.31 | 173.31 |
| A5_weighted_delay_train_min | 11137.2 | 11720.8 |
| A6_mean_exit_delay_min | 16.88 | 18.25 |
| A6_median_exit_delay_min | 0.82 | 0.82 |
| A6_p90_exit_delay_min | 62.58 | 66.87 |
| A9_mean_time_lost_min | 18.58 | 19.95 |
| B1_trains_exited | 193 | 193 |
| C1_freight_speed_kmh | 44.53 | 44.86 |
| C2_freight_detention_h | 14.43 | 12.93 |
| F1_unplanned_stops | 725 | 758 |

### DSS minus BL-0

| KPI | Delta |
|---|---|
| A2_section_punctuality_pct | +1.01 |
| A2_lop_count | -1 |
| A4_mean_arrival_delay_min | -1.47 |
| A4_max_arrival_delay_min | +0 |
| A5_weighted_delay_train_min | -583.64 |
| A6_mean_exit_delay_min | -1.37 |
| A6_median_exit_delay_min | +0 |
| A6_p90_exit_delay_min | -4.29 |
| A9_mean_time_lost_min | -1.37 |
| B1_trains_exited | +0 |
| C1_freight_speed_kmh | -0.33 |
| C2_freight_detention_h | +1.5 |
| F1_unplanned_stops | -33 |

Most affected trains (exit delay, min): HPCVHPKB19082025132943 +53.76, 12664@2025-08-19 -50.87, 18048@2025-08-19 -44.34
