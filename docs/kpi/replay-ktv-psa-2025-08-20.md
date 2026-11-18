# Closed-loop replay of 2025-08-20

Window 8-10 h, options by greedy, evaluation auto-accept, identical seed and demand under BL-0 and under the DSS (FR-SCN-05, FR-SCN-08).

## The loop

| What | Value |
|---|---|
| Clock ticks | 61 |
| Plans (cycles) | 48 |
| Longest gap between plans | 6 min |
| Candidate plans verified (G0-G3) / built | 9 / 48 |
| Recommendations released | 16 |
| Accepted (evaluation policy) | 16 |
| Control orders issued | 18 (0 declined) |
| Most options for one decision | 1 |
| Final statuses | DIVERGED 10, SUPERSEDED 6 |

## KPIs: DSS against BL-0

### day 2025-08-20

| KPI | DSS | BL-0 |
|---|---|---|
| A2_section_punctuality_pct | 63.64 | 63.64 |
| A2_lop_count | 36 | 36 |
| A4_mean_arrival_delay_min | 18.91 | 18.92 |
| A4_max_arrival_delay_min | 173.31 | 173.31 |
| A5_weighted_delay_train_min | 11990.4 | 11720.8 |
| A6_mean_exit_delay_min | 18.8 | 18.25 |
| A6_median_exit_delay_min | 0.82 | 0.82 |
| A6_p90_exit_delay_min | 73.76 | 66.87 |
| A9_mean_time_lost_min | 20.5 | 19.95 |
| B1_trains_exited | 193 | 193 |
| C1_freight_speed_kmh | 44.86 | 44.86 |
| C2_freight_detention_h | 12.93 | 12.93 |
| F1_unplanned_stops | 746 | 758 |

### DSS minus BL-0

| KPI | Delta |
|---|---|
| A2_section_punctuality_pct | +0 |
| A2_lop_count | +0 |
| A4_mean_arrival_delay_min | -0.01 |
| A4_max_arrival_delay_min | +0 |
| A5_weighted_delay_train_min | +269.63 |
| A6_mean_exit_delay_min | +0.55 |
| A6_median_exit_delay_min | +0 |
| A6_p90_exit_delay_min | +6.89 |
| A9_mean_time_lost_min | +0.55 |
| B1_trains_exited | +0 |
| C1_freight_speed_kmh | +0 |
| C2_freight_detention_h | +0 |
| F1_unplanned_stops | -12 |

Most affected trains (exit delay, min): 12840@2025-08-19 +46.51, 12664@2025-08-19 +6.65, 58506@2025-08-20 +1.28
