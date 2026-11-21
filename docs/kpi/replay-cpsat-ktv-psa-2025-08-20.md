# Closed-loop replay of 2025-08-20

Window 8-9 h, options by CP-SAT (primary) + greedy, evaluation auto-accept, identical seed and demand under BL-0 and under the DSS (FR-SCN-05, FR-SCN-08).

## The loop

| What | Value |
|---|---|
| Clock ticks | 61 |
| Plans (cycles) | 38 |
| Longest gap between plans | 5 min |
| Candidate plans verified (G0-G3) / built | 69 / 76 |
| Recommendations released | 131 |
| Accepted (evaluation policy) | 103 |
| Control orders issued | 89 (0 declined) |
| Most options for one decision | 2 |
| Final statuses | DIVERGED 67, EXPIRED 11, PRESENTED 4, SUPERSEDED 49 |
| Rolling plan by CP-SAT / cycles | 32 / 38 (greedy fallback 6) |
| CP-SAT plans verified (G0-G3) / solved | 32 / 38 |
| CP-SAT delay objective <= greedy hint | 33 / 34 |
| Frozen decisions imposed (outside the IR) | 661 (0) |
| Plan churn per re-plan (mean, max) | 3.5, 19 |

## KPIs: DSS against BL-0

### day 2025-08-20

| KPI | DSS | BL-0 |
|---|---|---|
| A2_section_punctuality_pct | 62.63 | 62.63 |
| A2_lop_count | 37 | 37 |
| A4_mean_arrival_delay_min | 20.23 | 20.42 |
| A4_max_arrival_delay_min | 182.01 | 182.01 |
| A5_weighted_delay_train_min | 13895.5 | 14071.5 |
| A6_mean_exit_delay_min | 23.84 | 24.38 |
| A6_median_exit_delay_min | 4.17 | 4.17 |
| A6_p90_exit_delay_min | 84.45 | 83.36 |
| A9_mean_time_lost_min | 25.54 | 26.08 |
| B1_trains_exited | 193 | 193 |
| C1_freight_speed_kmh | 43.39 | 43.65 |
| C2_freight_detention_h | 19.49 | 18.27 |
| F1_unplanned_stops | 642 | 662 |

### DSS minus BL-0

| KPI | Delta |
|---|---|
| A2_section_punctuality_pct | +0 |
| A2_lop_count | +0 |
| A4_mean_arrival_delay_min | -0.19 |
| A4_max_arrival_delay_min | +0 |
| A5_weighted_delay_train_min | -176.02 |
| A6_mean_exit_delay_min | -0.54 |
| A6_median_exit_delay_min | +0 |
| A6_p90_exit_delay_min | +1.09 |
| A9_mean_time_lost_min | -0.54 |
| B1_trains_exited | +0 |
| C1_freight_speed_kmh | -0.26 |
| C2_freight_detention_h | +1.22 |
| F1_unplanned_stops | -20 |

Most affected trains (exit delay, min): KSLKJEA_19082025115439 +109.72, 12840@2025-08-19 +41.46, 12503@2025-08-19 -18.06
