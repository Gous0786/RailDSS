# DR-0008: M1 exit review

| Field | Value |
|---|---|
| Spec references | §13 M1 exit criteria; REC-02; DSC-10 (rev. v1.7); FR-SCN-05/08; FR-VER-06, FR-VER-13 |
| Issues | #92 (this review). Depends on #90, #91, #57, #76, #78, #82, #59 |
| Date | 2026-11-19 |
| Status | **Proposed** (accepted when the PR that adds it is merged) |
| Supersedes | n/a |
| Evidence commit | `main` at `3539b6d` (after #142). Network `KTV-PSA-2025-08-r2` |

## Decision

**M1 (baseline loop) is complete.** All seven §13 M1 exit criteria are met with command evidence below.
The deviations in the second section are accepted and carried to M2. Spec v1.8 marks M1 done.

## Exit criteria

| # | Criterion (spec §13) | Result | Evidence |
|---|---|---|---|
| 1 | One real day runs in the twin | **Pass** | `python -m raildss_twin run --date 2025-08-20`: 193 planned, 193 entered, 193 exited, 0 in progress, no deadlock. Trace `54c23561…`, 23.5 s. Under profile D1 the observation model emits 48,731 `obs.position`, 4,045 `obs.station`, 15,952 `obs.occupancy` and 2,520 `obs.block` records. The offline auditor runs **all 27 real days** with zero executed violations (`tests/verifier/test_auditor.py`, 27 passed) |
| 2 | v0 = 10 scenarios | **Pass** | `python -m raildss_twin scenario check`: "scenarios: 10 valid". `scenario run`: SCN-01, 02, 03, 05, 06, 12, 13, 16, 20 and 22 all "OK" with their recorded `gt.*` trace hashes. `python -m raildss_orchestrator scenarios --report docs/kpi/scenarios-v0.md` reproduces the committed report byte for byte |
| 3 | Closed-loop real-day replay with greedy recommendations | **Pass** | `python -m raildss_orchestrator replay --report docs/kpi/replay-ktv-psa-2025-08-20.md` on 2025-08-20, 08:00-10:00: 121 ticks, 81 plans, **81 of 81 candidate plans verified by G0-G3**, 37 recommendations released and accepted (evaluation policy), 43 control orders, 0 declined. DSS against BL-0: A5 weighted delay **13,211 against 14,071 train-min (-6.1 %)**, A6 P90 exit delay -19.1 min, unplanned stops -39 |
| 4 | 100 % mutation catch | **Pass** | `tests/verifier/test_mutation.py`: `test_mutation_catch_rate_is_100_percent` PASSED. Every rule flags its mutant at its effective severity (53 mutation tests passed). The rule catalogue lint passes in CI |
| 5 | TLA+ 0 violations | **Pass** | CI job "TLA+ model check and conformance", run 44566954048 (head `c03be9e`, merged as `3539b6d`): `HitlLifecycle` "Model checking completed. No error has been found." (13,755 states, 7,039 distinct); `HitlLifecycleRivals` "No error has been found." (26,865 states, 10,939 distinct). Implementation conformance `tests/hitl`: 23 passed |
| 6 | First SME face-validity review, with the historical chart comparison | **Pass** | [docs/review/m1-face-validity.md](../review/m1-face-validity.md) (#91): historical chart comparison and findings FV-01 to FV-06. Open findings are carried below |
| 7 | REC-02 fallback decision recorded | **Pass** | [DR-0004](0004-od-33-mvp-extent.md): the full KTV-PSA section is the MVP extent, and the VZM-PSA fallback is not taken (CP-SAT latency spike, #76) |

Supporting checks run for this review:

- **Calibration** (`python -m raildss_twin calibrate`): "calibration: OK". Freight 59 of 64 blocks within
  ±15 % (92.2 %, target 90 %). Passenger 98 of 307 spans within 1 min (31.9 %; see D1). Pass-through
  is never slower than stop-start. 102 reference runs reproduce to 1 s.
- **Data:**
  - `python -m raildss_etl dq --check`: "dq: OK".
  - `raildss network publish --check`: "network publication: OK (data/published/KTV-PSA-2025-08-r2)".
  - `kpi-reference --check`: OK.
  - `tools/manifest.py --check`: "OK (4 dataset dirs)".
  - `raildss network show` counts 11,175 records, including `train_run` 2,342 and `consist` 43.

## Deviations (accepted, carried to M2)

| ID | Deviation | Evidence | M2 handling |
|---|---|---|---|
| D1 | **Passenger timing (DSC-10).** 31.9 % of spans are within 1 min of the train's own booked time, against the 95 % target. Any per-leg model has a ceiling of about 52 %, because the WTT books per-train pathing time (for example PUN→PSA at 1,140 s for some trains and 540 s for others) | [DR-0005](0005-dsc10-passenger-per-train-reference.md), [passenger residual](../twin/passenger-residual.md), [calibration report](../twin/runtime-calibration.md) | Product-owner acceptance of the residual, or per-train timing data (M2-01) |
| D2 | **BL-0 freight has no detention model.** For the same 86 non-stabled loads the section time is a median 0.93 h in BL-0 against 6.22 h in the log. The twin adds no crew changes, path waits or long halts (FV-02) | [BL-0 reference check](../kpi/bl0-reference-check-2025-08-20.md) | Freight halts from the K1 dwell statistics (M2-02) |
| D3 | **Face-validity findings FV-01 to FV-06** are open. FV-01: position and `waiting` state flapping. FV-02: freight too fast (D2). FV-03: the passenger delay tail cannot be checked without actuals. FV-04: most released recommendations end DIVERGED. FV-05: the DSS makes some trains later. FV-06: one day and one window | [face-validity review](../review/m1-face-validity.md) | M2-03 (FV-01), M2-02, M2-04 (FV-04/05), M2-05 (FV-06), FV-03 waits for data |
| D4 | **Planner gaps.** The planner does not model TSRs, point failures or late-train disruptions. The loop is exercised on the line-block scenarios only (SCN-05, SCN-20). The other v0 scenarios run under BL-0 | [scenario KPIs](../kpi/scenarios-v0.md) | M2-06 |
| D5 | **Narrow demo.** The DSS benefit (A5 -6.1 % at this commit; -4.9 % in the face-validity review) comes from one 2-hour window on one day. It is not a benefit claim. On re-run, 30 of 37 released recommendations end DIVERGED and 7 SUPERSEDED (the review had 26 of 32 diverged). Two freight trains are +110 and +74 min later | [replay report](../kpi/replay-ktv-psa-2025-08-20.md) | M2-04, M2-05 |

The replay report was regenerated by this review: the committed one predated #137 and #66. The scenario
report reproduced byte for byte.

## Consequences

- The M2 carry-over is drafted in the PR for this record (M2-01 onwards), each item with a problem, a
  scope and an acceptance criterion. GitHub issues are created by the project owner.
- Spec v1.8 marks M0 and M1 done, with these deviations.
- The README and the [documentation index](../README.md) describe the system as built
  ([architecture.md](../architecture.md)).
