# M2 face-validity review, round 2 (issue #171, DSC-11, spec §12.4, OD-05)

Second review of the twin and the DSS, after the twin fixes T1 (#144, position and state flapping) and T2 (#145, freight
detention), the verifier rules for speed restrictions and single-line working (#151), the CP-SAT rolling horizon and
the three-line, sub-block and disruption options (#152 to #156). It re-checks FV-01 to FV-06 of
[round 1](m1-face-validity.md) on five charts: three real days and two disruption scenarios. There is no SME (OD-05),
so the reviewers are the project team; public sources only, no scraping, no departmental contact.

Evidence, all in the repository:
- Charts: [`m2-face-validity.svg`](m2-face-validity.svg), five charts in six panels. 1a freight of 2025-08-20 against the
  freight log, 1b passenger of 2025-08-20 against the WTT, 2 and 3 the real days 2025-08-12 and 2025-08-22, 4 the
  scenario SCN-05 (line block CPP-SGDM, single-line working), 5 the scenario SCN-08 (OHE block). Round 1's chart is
  [`m1-face-validity-2025-08-20.svg`](m1-face-validity-2025-08-20.svg) for the same day, to compare panel by panel.
- The charts and every number in section 1 come from `tools/face_validity_round2.py` on the chart exports of the twin
  (BL-0, seed of the day or of the scenario): `python -m raildss_twin chart --date D`, `python -m raildss_orchestrator
  whatif SCN-05 --span-min 20`.
- Decision loop on the current twin: section 3, from `python -m raildss_orchestrator replay --date 2025-08-20 --from-h 8
  --to-h 10 --tick-s 120` and `multiday run --from-h 8 --span-h 2` (BL-1 on 3 days, BL-2 on 1 day).
- Unchanged from the merged reports: [`bl0-reference-check-2025-08-20.md`](../kpi/bl0-reference-check-2025-08-20.md),
  [`freight-detention-dsc20.md`](../kpi/freight-detention-dsc20.md), [`multi-day-replay.md`](../kpi/multi-day-replay.md)
  (the last was run before T1 and T2 and is superseded by section 3 until it is re-run).

## 1. The five charts

| # | Chart | What the twin does | Judgement |
|---|---|---|---|
| 1 | **2025-08-20**, freight against the log (1a), passenger against the WTT (1b) | 193 trains, all leave the section. Freight now halts at intermediate stations (grey and blue lines both show long horizontal halts). Passenger median speed 71.6 km/h, p95 106.1, maximum 120.6. Passenger exit delay median 5.0 min, mean 19.9, 55 of 99 within 5 min, 13 over 60 min, longest 220 min. Freight speed median 49.1, p95 58.8, maximum 60.1 | **Plausible.** No spikes, no speed above the line speed (round 1: 356.8 km/h). Freight now spends hours at stations as the log does; section time is still shorter than the log's (median 4.37 h against 6.22 h). The passenger tail cannot be checked (FV-03). The evening bunch of delayed passenger trains at VZM (16 to 19 h) is the largest feature and looks like a real knock-on of freight held at the junction, but only an SME can say whether the size is right |
| 2 | **2025-08-12**, twin only | 194 trains, all exit. Passenger exit delay median 0.5 min, mean 5.3, longest 83 min, 3 over 60 min. Freight median speed 48.1 km/h | **Plausible.** The calmest of the days: a light freight load (106 loads, 18 long through-runs) and a short tail, as the log's median through-transit of 6.8 h suggests |
| 3 | **2025-08-22**, twin only | 204 trains, all exit. Passenger exit delay median 4.5, mean 13.8, longest 174.5 min, 6 over 60 min. Freight median speed 48.6 | **Plausible.** Between the two days above, a busy afternoon at VZM and KTV with freight halts; nothing in the chart looks impossible (no spikes, speeds within the line speeds) |
| 4 | **SCN-05**, DN line blocked CPP-SGDM, single-line working on the UP line, 10:00 to 12:23 | 172 trains, all exit. The shaded block (65.4 to 78.6 km) is crossed by fewer trains on the UP line only; the chart shows trains stopping before it during the block and moving again after it. How fast the queue clears was not measured. Passenger exit delay median 2.0 min, longest 74.5, 3 over 60 min | **Plausible.** Trains crossing the block during single-line working are fewer and later, which is what a controller would expect, and the delays shrink after the end (longest 74.5 min). The committed export `web/public/data/SCN-05.json` was **not** this: it was made before T1 and still shows the flapping (349 jumps over 3 km, a passenger speed of 1,232 km/h, longest delay 218 min). It is stale and must be regenerated (finding FV-08) |
| 5 | **SCN-08**, OHE block 10:00 to 12:00 | 179 trains, all exit. Passenger exit delay median 5.0 min, mean 23.6, longest 204 min, 14 over 60 min. Freight median speed 48.5 | **Plausible, with the longest tail of the five.** The heaviest of the disruption scenarios (A5 15,502 on the older twin) and a long tail is expected from a two-hour OHE block, but its size is not checked against anything. Worth an SME's view on how many passenger trains should be over an hour late |

## 2. Status of FV-01 to FV-06

| ID | Round 1 | Round 2 | Evidence |
|---|---|---|---|
| FV-01 | medium: positions jump back and forth | **Closed on the nominal days and the OHE scenario; open in the committed SCN-05 file** | 0 jumps over 3 km and 0 single-tick spikes in 80,936 (08-20), 61,240 (08-12), 77,736 (08-22), 66,250 (SCN-05, fresh) and 70,073 (SCN-08) state intervals; speeds never above 120.6 km/h (passenger) and 60.1 km/h (freight). `tests/twin/test_state_jumps.py` keeps it. The stale SCN-05 export is FV-08 |
| FV-02 | medium: BL-0 freight far faster than the log | **Addressed, within DSC-20** | 08-20, same loads: median section time 4.37 h in BL-0 against 6.22 h in the log (mean 4.96 against 6.79, p90 11.40 against 11.63). Over 27 real days the median through-transit is 8.02 h against BL-5's 10.41 h (-23 %, inside ±25 %). A gap remains (loco and crew availability, yards are not modelled) |
| FV-03 | medium: passenger delay tail cannot be checked | **Open, waits for data** | Still no passenger actuals. The tail is now: over 60 min late on 13 of 99 trains (08-20), 3 of 88 (08-12), 6 of 104 (08-22), 3 of 77 (SCN-05) and 14 of 89 (SCN-08). Quote DSS against BL-0 only. Question for the data owner stays on the list |
| FV-04 | low: 26 of 32 recommendations end `DIVERGED` | **Open, slightly worse** | 08-20 replay, 8 to 10 h: DIVERGED 47 of 53 released (89 %), EXPIRED 3, SUPERSEDED 2, ORDER_ISSUED 1. The lifecycle (`raildss_hitl/lifecycle.py`, `state_changed`) diverges every plan in force when the estimator's version changes, and the loop replans every 3 to 4 min; so a plan seldom survives to completion. That is by design of the state machine (TLA+ checked) but it says little about plan quality: the metric to read is whether the orders issued are executed, not how a recommendation ends. Follow-up: report "orders executed as issued" next to the final status |
| FV-05 | low: DSS makes some trains later, one freight train +54 min | **Open, larger** | Most affected trains in the same replay: HDCGVSPS17082025170856 +345 min, MMHRMCS_19082025184346 +341 min, EMTYTLHR19082025222850 +320 min (freight that entered the day before). These are the trains the greedy planner holds longest, and the holds are legal under the rules. No per-train cap exists in the policy. A controller would not accept a hold of five hours to save passenger minutes. Follow-up: a per-train cap or a priority aging term in the policy |
| FV-06 | low: one day and a two-hour window | **Partly addressed, evidence is stale** | Five real days are in `multi-day-replay.md` (6 to 12 h), but run before T1 and T2; the evening peak is not covered. A short re-run on the current twin (section 3) changes the answer: the full re-run is needed (10 runs of 40 to 110 min each, within the 2 h run limit) |

## 3. The decision loop on the current twin (window 8 to 10 h)

BL-1 is the greedy loop, BL-2 the CP-SAT loop, both with auto-accept, against BL-0 on the same day, demand and seed. These
are short evidence runs (2 h window, one seed), not an evaluation: a difference of a few per cent is within what one seed
can show.

| Day | Arm | Plans verified / built | Accepted / orders | A5 BL-0 | A5 arm | Difference | Punctuality | p90 exit delay | Unplanned stops | Freight detention (h) |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 2025-08-12 | BL-1 | 52 / 65 | 53 / 52 | 16,620.0 | 17,132.4 | **+512.4** | 82.95 = 82.95 | 32.4 to 36.1 | 385 to 351 | 206.1 to 207.1 |
| 2025-08-20 | BL-1 | 66 / 66 | 50 / 60 | 30,512.6 | 32,117.5 | **+1,604.9** | 65.66 to 67.68 | 84.9 to 64.2 | 585 to 551 | 282.3 to 305.3 |
| 2025-08-22 | BL-1 | 43 / 65 | 21 / 26 | 24,533.0 | 25,434.0 | **+901.1** | 66.35 to 64.42 | 48.4 to 58.2 | 660 = 660 | 256.4 to 255.6 |
| 2025-08-20 | BL-2 | 50 / 116 | 82 / 88 | 30,512.6 | 30,096.0 | -416.6 | 65.66 = 65.66 | 84.9 to 83.4 | 585 to 569 | 282.3 to 280.2 |

Executed with 0 head-on and 0 deadlocks in all four. Reading:

- **BL-0 changed a lot with T2.** A5 of BL-0 on these days was 6,933 (08-12), 14,071 (08-20) and 9,967 (08-22) in the
  multi-day report; it is now 16,620, 30,513 and 24,533 over the day, because a detained freight train now counts as late.
  Every earlier DSS-against-BL-0 number was made against the old baseline and does not carry over.
- **The greedy loop no longer helps A5 (FV-07, below).** Round 1 had it lowering A5 by 4.9 % on 08-20; the multi-day report
  had it lower on 4 of 5 days. On the current twin it raises A5 on all 3 days (+3 %, +5 %, +4 %), while still cutting
  unplanned stops on two days and the p90 exit delay on 08-20. The direction the policy intends (passenger above
  freight) shows only in the p90 and the stops, not in the weighted delay.
- **CP-SAT is slightly better than BL-0 on 08-20** (-1.4 %), but with 50 of 116 candidates verified: it builds many plans
  the gates reject.

## 4. New findings

| ID | Severity | Finding | Evidence | Follow-up |
|---|---|---|---|---|
| FV-07 | **high** | **After T1 and T2 the greedy loop raises the weighted delay on every day tried** and the DSS-against-BL-0 benefit of round 1 and of the multi-day report is gone. A5 now includes freight held at stations (detention counts), and the loop's holds probably add to it (not traced) | Section 3: +512, +1,605 and +901 train-min. BL-2 gives -417 on one day | Re-run the five-day report on the current twin; look at what the planner trades (passenger gain against freight loss), whether the policy's weights are right for a baseline that now includes detention, and whether the holds interact badly with the T2 halts. Until then do not quote the old multi-day results |
| FV-08 | medium | **Candidate plans and what-if variants fail the connection rule after T2, and the committed SCN-05 export is stale.** Rebuilt on the current twin, both SCN-05 options (BL-1 and DSS) fail the audit with 10 violations of VER-CON-01 (reception and despatch only along Connections edges) and a weighted-delay change of +188 min; the committed file, made before T1, shows both as PASS with 0.0 and +71.5 min. In the loop, 13 of 65 (08-12) and 22 of 65 (08-22) greedy candidates and 66 of 116 CP-SAT candidates are rejected, where the multi-day report had 100 % for BL-1 | `docs/kpi/multi-day-replay.md` (before) and section 3 (after); SCN-05 export rebuilt with `whatif SCN-05 --span-min 20` | Probable cause, not traced: a detained freight train waits on a loop (#145) and leaves along an edge that is not a Connections edge. Find the violating moves, fix the twin or the plan, then regenerate `web/public/data/SCN-05.json` (not done here: the rebuilt file fails `export.test.tsx` on purpose, since its variants no longer verify) |

## 5. Follow-ups to open as issues

1. **FV-07 and FV-08 first** (they change what the demo can claim): trace the VER-CON-01 violations after T2, regenerate
   the SCN-05 export, re-run the five-day multi-day report on the current twin, and review the policy weights.
2. **FV-05:** per-train cap or aging in the policy.
3. **FV-04:** report "orders executed as issued" beside the final status.
4. **FV-03:** passenger actuals, if the data owner can supply them. Not blocking.
5. **Evening peak:** add a 16 to 22 h window to the multi-day report, once FV-07 is understood.

## 6. Limits of this review

- No SME: nobody has judged the twin's crossing choices or the DSS's orders against operating practice.
- Plausibility is read from the charts and from counts (speeds, jumps, delays, order of trains); a domain expert should
  look at the evening bunch at VZM on 08-20 and at the OHE-block tail on SCN-08.
- The historical chart exists for freight only (the log); the WTT is the booked reference for passenger trains.
- The section 3 runs are one seed and a 2 h window; they show direction, not size.
