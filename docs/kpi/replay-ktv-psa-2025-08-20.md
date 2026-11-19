# Closed-loop replay of 2025-08-20

Window 8-10 h, options by greedy, evaluation auto-accept, identical seed and demand under BL-0 and under the DSS (FR-SCN-05, FR-SCN-08).

## The loop

| What | Value |
|---|---|
| Clock ticks | 121 |
| Plans (cycles) | 81 |
| Longest gap between plans | 5 min |
| Candidate plans verified (G0-G3) / built | 81 / 81 |
| Recommendations released | 37 |
| Accepted (evaluation policy) | 37 |
| Control orders issued | 43 (0 declined) |
| Most options for one decision | 1 |
| Final statuses | DIVERGED 30, SUPERSEDED 7 |

## KPIs: DSS against BL-0

### day 2025-08-20

| KPI | DSS | BL-0 |
|---|---|---|
| A2_section_punctuality_pct | 61.62 | 62.63 |
| A2_lop_count | 38 | 37 |
| A4_mean_arrival_delay_min | 18.79 | 20.42 |
| A4_max_arrival_delay_min | 182.01 | 182.01 |
| A5_weighted_delay_train_min | 13211.2 | 14071.5 |
| A6_mean_exit_delay_min | 22.38 | 24.38 |
| A6_median_exit_delay_min | 4.17 | 4.17 |
| A6_p90_exit_delay_min | 64.27 | 83.36 |
| A9_mean_time_lost_min | 24.08 | 26.08 |
| B1_trains_exited | 193 | 193 |
| C1_freight_speed_kmh | 43.1 | 43.65 |
| C2_freight_detention_h | 20.89 | 18.27 |
| F1_unplanned_stops | 623 | 662 |

### DSS minus BL-0

| KPI | Delta |
|---|---|
| A2_section_punctuality_pct | -1.01 |
| A2_lop_count | +1 |
| A4_mean_arrival_delay_min | -1.63 |
| A4_max_arrival_delay_min | +0 |
| A5_weighted_delay_train_min | -860.28 |
| A6_mean_exit_delay_min | -2 |
| A6_median_exit_delay_min | +0 |
| A6_p90_exit_delay_min | -19.09 |
| A9_mean_time_lost_min | -2 |
| B1_trains_exited | +0 |
| C1_freight_speed_kmh | -0.55 |
| C2_freight_detention_h | +2.62 |
| F1_unplanned_stops | -39 |

Most affected trains (exit delay, min): KSLKJEA_19082025115439 +109.72, HPCVHPKB19082025132943 +73.89, 12664@2025-08-19 -56.54
