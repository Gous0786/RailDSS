# M1 face-validity review (issue #91, spec §13 M1 exit, OD-05)

Review of the twin and the DSS on a real day, 2025-08-20 on KTV-PSA, against the historical freight log,
the booked passenger timetable (WTT), the KPI reference (K1) and public sources. There is no SME (OD-05), so
the reviewers are the project team; the findings below are what a domain expert should look at again.
The DSS side is the replay after PR #131 (VER-FRZ-01: frozen decisions imposed only when the world changed).

Evidence, all in the repository:
- Chart comparison: [`m1-face-validity-2025-08-20.svg`](m1-face-validity-2025-08-20.svg), panel A freight (log
  against twin), panel B passenger (WTT against twin). The SVG is drawn from `web/public/data/2025-08-20.json`
  (the twin, BL-0) and the raw log `WAT_GOODS_TRAIN_AUG25_01082025_27082025.csv`.
- BL-0 against the K1 reference: [`docs/kpi/bl0-reference-check-2025-08-20.md`](../kpi/bl0-reference-check-2025-08-20.md)
  (`python -m raildss_twin bl0-check`).
- DSS against BL-0: [`docs/kpi/replay-ktv-psa-2025-08-20.md`](../kpi/replay-ktv-psa-2025-08-20.md)
  (`python -m raildss_orchestrator replay --tick-s 120 --report ...`; window 08:00-10:00, greedy options,
  evaluation auto-accept).
- Public sources: the KK-line, HCM and boundary reviews ([`docs/etl/boundary-review-ktv-psa.md`](../etl/boundary-review-ktv-psa.md),
  `docs/etl/mapping-ktv-psa.md`). No scraping, no departmental contact.

## 1. What looks right

| Check | Result |
|---|---|
| Passenger trains follow the WTT | 99 trains left the section. The median exit delay is 0.8 min and the median running time is 1.0 min over the booked one. 58 of 99 trains exit within 5 min of the booked time, 30 are 5-60 min late, 11 are more than 60 min late |
| Speeds | Between 30 s state ticks, leaving out the 510 of 47,582 intervals (1.1 %) that jump more than 3 km (FV-01): passenger median 81.0 km/h, p95 110.0 km/h; freight median 50.6 km/h, p95 60.0 km/h, maximum 60.1 km/h. The passenger maximum, 356.8 km/h, comes from smaller jumps of the same kind |
| Freight demand | The twin enters the 94 loads of the day at their logged entry times and every load leaves the section (193 trains exited, none stuck) |
| Direction of the DSS effect | Against BL-0 the DSS lowers passenger delay and slightly worsens freight, as the policy weights (passenger above freight) intend: weighted delay 11,140.9 against 11,720.8 train-min (-4.9 %), mean exit delay 16.89 against 18.25 min, p90 exit delay 62.58 against 66.87 min, punctuality 64.65 % against 63.64 %. Freight detention rises 12.93 to 14.43 h and freight speed falls 44.86 to 44.53 km/h |
| Verification | Every candidate plan is checked by G0-G3: 53 of 53 plans verified at 120 s ticks (80 of 80 at the default 60 s). The KPI tables are identical at both tick lengths |
| Public geography | The KK line, the Vizianagaram-Rayagada line and the Visakhapatnam-side stations in the log are in the order public sources give (boundary review); HCM is a halt without a loop |

## 2. Findings

| ID | Severity | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| FV-01 | medium | **The twin reports positions that jump back and forth.** A moving train alternates between `running` at its true position and `waiting` at a station km 3 or more km away, tick after tick. The chart shows spikes and the KPI engine counts each `waiting` tick as a new stop | On 2025-08-20 BL-0: 249 single-tick spikes in 39 of 193 trains and 510 of 47,582 state intervals (1.1 %) with a jump over 3 km. 147 of the 758 `waiting` starts (19 %, in 36 trains) are such jumps, so `F1_unplanned_stops` is overstated by up to 147 and the DSS-against-BL-0 difference of -33 stops (725 against 758) could lie within that The spikes sit at station kms (ALM 16.97, KUK 24.08, KPL 7.74) | Find the cause in `Sim.position` / the `waiting` status during block claims (`raildss_twin/sim.py`); keep a test that no train changes km by more than its speed allows between ticks; recompute the KPIs. The chart shows the spikes today. **Resolved (#144):** a train waiting at the signal of the next automatic sub-block (or for a station line) had run its last segment, and `Sim.position` fell back to the km of the station it had left. It now stands at the segment's end. On 2025-08-20 no `gt.state` interval jumps more than 3 km (`tests/twin/test_state_jumps.py`; 510 before). The `waiting` ticks are real stops at automatic signals that were only drawn at the wrong km, so `F1_unplanned_stops` is unchanged (662 under BL-0) |
| FV-02 | medium | **BL-0 freight is far faster than the real freight.** The twin has no crew changes, path waits or long halts that the log shows | Same 86 non-stabled loads: median section time 6.22 h in the log, 0.90 h in BL-0 (mean 6.79 against 2.09 h, p90 11.63 against 3.66 h). Panel A of the SVG shows grey horizontal halts of hours where the blue lines run on | Add freight dwell at stations from the K1 dwell statistics (median and p90 per station) to the twin, and re-run BL-0 and the replay. Until then BL-0 is a relative reference only (see the BL-0 check note) |
| FV-03 | medium | **BL-0 passenger delay has a long tail that cannot be checked.** Mean 18.2 min against median 0.8 min; 11 trains are more than an hour late, the longest 158.6 min | The dataset has no passenger actuals, so no historical passenger delay exists to compare with (K1: `passenger_delay` not available). | Needs passenger actuals or an SME's view of the typical delay on the day. Until then quote DSS against BL-0 only. Record the question for the data owner (no departmental contact is made by the project) |
| FV-04 | low | **26 of the 32 released recommendations end `DIVERGED`** and 6 `SUPERSEDED`: the plan the controller accepted is no longer what the world does by the time it is checked | `docs/kpi/replay-ktv-psa-2025-08-20.md`, "Final statuses". The meaning of `DIVERGED` against a replan every 5-6 min should be checked: with automatic acceptance and orders executed by the twin, a high share is unexpected | Check how `DIVERGED` is decided and against what tolerance; tune or document |
| FV-05 | low | **The DSS makes some trains later.** The most affected trains are +53.8 min for one freight train (HPCVHPKB19082025132943) and -50.9 and -44.3 min for two passenger trains of the previous day | Same report. The gain is on passengers; the freight loss is large for one train | Check the freight train against the hard rules (does the plan hold it for a reason a controller would accept?); consider a per-train cap in the policy |
| FV-06 | low | **One day and a two-hour window.** The comparison covers 2025-08-20, and the DSS acts only from 08:00 to 10:00 | Replay command and report header | Replay a few more days from `web/public/data` (2025-08-12 and others) and the evening peak once FV-01 and FV-02 are fixed |

## 3. Follow-ups to open as issues

1. **Twin: position and `waiting` state flapping (FV-01).** Fix, add the jump test, regenerate `docs/kpi/*` and
   `web/public/data/*.json`. Highest value: it affects the chart, the stop count and every KPI that uses
   standing time.
2. **Twin: freight halts from the K1 dwell statistics (FV-02).** Needed before BL-0 can stand for the real
   day. Re-run the BL-0 check and the replay afterwards.
3. **DSS: why 26 of 32 recommendations diverge (FV-04), and the one freight train held 54 min (FV-05).**
4. **Replay more days and a longer window (FV-06).**
5. **Passenger actuals (FV-03).** If the data owner can supply them, replace the passenger part of the BL-0
   check with a per-train comparison. Not blocking M1.

## 4. Limits of this review

- No SME: nobody has judged the twin's crossing choices or the DSS's orders against operating practice.
- The historical chart is the freight log and the WTT; no historical passenger chart exists in the dataset.
- Public sources cover geography and the network, not operating decisions.
- One day, one 2-hour DSS window, greedy options (no CP-SAT in the replay), automatic acceptance.
