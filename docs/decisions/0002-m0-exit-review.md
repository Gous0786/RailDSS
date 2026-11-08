# DR-0002: M0 exit review

| Field | Value |
|---|---|
| Issue | #25 (M0-25) |
| Spec references | §13 M0 exit criteria, DQ-10, DQ-14…22, DSC-10 (rev. v1.5), FR-DATA-04, §12.3, RK-06 |
| Date | 2026-11-08 |
| Evidence commit | `main` at `28f377a` (merge of #47) |
| Status | **Accepted** on 2026-11-08. The project owner accepted the DSC-10 decision in §3 (documented passenger shortfall accepted as met for M0, CHE-PSA flagged for SME review) |
| Supersedes | n/a |

## 1. Summary

M0 meets every exit criterion except the passenger part of DSC-10, which is not met: 53 of 191 unpadded
WTT spans (27.8 %) are within ±1 min, against a target of 95 %. The user approved the definition and
the way a shortfall is handled (spec v1.5). **On 2026-11-08 the owner accepted the documented shortfall as
DSC-10 met for M0, with the CHE-PSA spans flagged for SME review** (§3).

M0 closes with this deviation recorded. Carry the passenger calibration into M1 as
follow-up F01. It does not block the M1 work that depends on M0: the twin, the verifier and the
latency spike all use the published network and the freight calibration, which pass.

## 2. Acceptance criteria (#25)

Every command was run on `28f377a` on 2026-11-08 and exited 0.

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | DQ-14…22 PASS in the committed DQ report (#17) | **PASS** | `python -m raildss_etl dq --check`: DQ-14 to DQ-22 all `PASS`; the report equals the committed baseline `docs/etl/dq/ktv-psa-baseline.*` |
| 2 | Network validator 0 errors, `KTV-PSA-2025-08` published (#18) | **PASS** | See the row details below |
| 3 | DSC-10 calibration met (#16) | **MET for M0 by owner decision: freight met, passenger shortfall documented and accepted** | See the row details below |
| 4a | Chart licence decision recorded (#23) | **PASS** | [DR-0001](0001-licence-review-m0.md) §3 (D2 train-graph component). Status Accepted (PR #30 merged 2026-11-04) |
| 4b | Dummy-data catalogue complete (#24) | **PASS** | [`docs/data/dummy-data-catalogue.md`](../data/dummy-data-catalogue.md); `tests/params/test_dummy_catalogue.py` checks that every DUMMY register key is catalogued, with a source and a check date, and passes in CI |
| 5 | CI green on `main`; open risks and deviations noted for M1, including the M1 latency spike | **PASS** | CI run 42454599980 on `28f377a`: success. Risks and deviations are in §5 and §6 of this record |

**Row 2 details.**
- **Publication:** `raildss network publish --check` reports `validator: 0 errors (N-01 … N-07)`. The rebuild is byte-identical to the committed publication.
- **Hash:** network hash `a9a2f57399d34066f4813e12a0068e479fccf52d148fd0edb4458c217450b77d`.
- **Contents:** `raildss network show KTV-PSA-2025-08` reports 8,773 records with the hash verified. These include:
  - 22 operational points, 50 block sections, 76 block lines, 103 station lines and 392 connections;
  - 8 PSRs, 338 signals, 210 sub-blocks, 232 gradient records and 130 curve records;
  - 250 services, 4,260 timetable points, 2,604 freight loads and 27 day instances.

**Row 3 details.** `python -m raildss_twin calibrate`:
- **Freight:** 59 of 64 blocks with at least 30 samples are within ±15 % of the log p10 (92.2 %, target ≥ 90 %).
- **Pass-through vs stop-start:** pass-through is never slower than stop-start (0 blocks).
- **Reproducibility:** results are reproducible to 1 s.
- **Passenger:** 53 of 191 unpadded spans are within ±1 min (27.8 %, target ≥ 95 %). 116 spans are padded, and 77 of all 307 spans are within ±1 min.
- **Worst cases:** the worst 10 blocks and spans are in [`docs/twin/runtime-calibration.md`](../twin/runtime-calibration.md).

### Also checked (moved to M1 by the 3 Nov schedule decision, but now on `main`)

| Item | Result | Evidence |
|---|---|---|
| One real day runs in the twin (#20, merged as #46) | **PASS, with one train excluded** | See the details below |
| DISPLIB-style IR converter (#22, merged as #47) | Present | `python -m raildss_twin displib` exists. The converter is outside the scope of this review and is not re-checked |
| Scenario library v0, 10 scenarios (#21) | Not started | M1, as scheduled |

**Twin day-run details.** `python -m raildss_twin run`, 2025-08-20, seed 0, headless:
- 192 planned, 192 entered, 192 exited, 0 in progress, 0 CSR exceedances;
- 10.0 s wall time, trace hash `6fd6dae0…`.
- Train 22501 is reported UNSUPPORTED because its path starts or ends at a halt (F10).

## 3. Decision

The owner accepted this documented shortfall as DSC-10 met for M0 (2026-11-08), on the condition that the
CHE-PSA spans are flagged for SME review (below). M0 closes with one recorded deviation:

| Deviation | Cause | Handling |
|---|---|---|
| DSC-10 passenger: 27.8 % of unpadded spans within ±1 min, target 95 % | The model has no station approach. Trains that stop at PSA, CHE or VZM are slowed by turnout and loop speeds on reception, which non-stop legs (where the supplements are fitted) do not show. The worst spans (CHE→PSA, VZM→…) are 6–9 min too fast | Follow-up F01 in M1. Per-span fitting stays excluded (user decision, spec v1.5) |

**Flagged for SME review: the CHE-PSA span of 9 legs, down direction.** It is all ten of the worst spans in the
calibration report (trains 20842, 22842, 02812, 12515, 22643 twice, 22973, 20854, 22860 and 12659). The booked time
is 3,180 s and the twin gives 2,612 s to 2,691 s (physics plus 280 s of supplements), 489 s to 568 s too fast. The
question for the SME is what the 3,180 s contains beyond running time: dwell at the intermediate halts, a speed
restriction, or part of the booked path that is not in the dataset (this tests the station-approach explanation
above). The report lists only the ten worst spans; the other 128 scored spans outside the band are not itemised
(part of F01).

## 4. Independent re-check of PRs merged without an AO review

The merged code and tests were read on `main`. Some suspicions were checked against the data; these
are noted as "probed". Defects become M1 follow-ups F-xx (drafted in the PR for this record). Nothing
is fixed here.

| PR | Scope | Findings |
|---|---|---|
| #34 | WTT import and dated expansion (#10) | **No defect found.** Checked: the Monday-first masks; `DAY_OF_RUN` and `BLOCK_BUSY_DAYS` (DQ-19 passes on all 4,260 rows); `runs_on` origin-date logic. Probed: `runs_on` takes 0.15 s per day (not a performance risk) |
| #36 | Freight demand and statistics (#11) | **No defect found.** Checked: the block times are recomputed from timestamps (DQ-21); the loads per day agree with R06 (within 7 %); every wagon code in the log maps to a rolling-stock family (`unmapped = []`). Observation: per-load first and last rows rely on log order, which DQ-21 guarantees |
| #42 | Boundary flows, review fix round (#12) | **F11, F12.** The tamper tests for both cross-checks are sound. Probed: no freight exit falls outside the 27 entry days |
| #43 | Running-time calibration (#16) | **F01, F02, F03, F04** |
| #44 | DQ report and CI gate (#17) | **F05** |
| #45 | Network publication (#18) | **F06, F07, F08, F09.** Probed: all 50 blocks have gradient records today, so F07 is a missing guard, not missing data |
| #46 | Twin skeleton and direction-gate fix (#20) | **Direction gate: no defect found.** Checked: claims and releases balance on every path (immediate grant, wait then grant, woken by the gate then retry); the sim interrupts no process, so a claim cannot leak; the new tests cover the same-instant race, queue retention and the deadlock false positive. **Twin: F10** |

### Findings

| ID | Source | Severity | Finding |
|---|---|---|---|
| F01 | #43 | High | Passenger calibration is short of DSC-10 (§3). Root cause: no station-approach model at stops |
| F02 | #43 | Medium | Freight calibration uses one reference consist (BOXN) for every wagon type, while the log p10 mixes types. Per-family templates are not used |
| F03 | #43 | Medium | The running-time track applies every PSR of a direction to every train, ignoring the PSR's lines (for example 5440, 15 km/h over the VZM diamond, lines DN/MID) |
| F04 | #43 | Low | `CalibrationReport.reproducible` is an in-process probe on one block. The real guarantee is the committed-vs-fresh test, so the field over-states what it checks |
| F05 | #44 | Medium | The DQ baseline includes the parameter register content hash, so any unrelated parameter edit fails `dq --check`. This happened in #43 |
| F06 | #45 | Medium | `network_version.json` embeds the DQ report, including the register hash. A parameter edit changes the hash of `KTV-PSA-2025-08` without a version bump, and `publish --check` then forces a regeneration under the same label |
| F07 | #45 | Low | The validator has no coverage rule: every COA block has gradients, and every block line has a speed. Today the data is complete, but nothing enforces it |
| F08 | #45 | Low | N-05 checks `network_version` and `timetable_version` only. `policy_version` and `rule_catalogue_version` are unstamped by design (provenance card), and FR-DATA-04 does not say static tables are exempt |
| F09 | #45 | Medium | Published layers are incomplete: `train_run` (the dated WTT expansion) and the rolling-stock tables from #15 (`loco_class`, `vehicle_type`, `consist`) are empty. The twin rebuilds them in memory |
| F10 | #46 | Medium | The twin rejects trains whose path starts or ends at a halt (22501 on 2025-08-20), so a "real day" is not complete |
| F11 | #42 | Low | Acceptance windows are daily only (no weekday or date range), which limits boundary-closure scenarios for the scenario library |
| F12 | #42 | Low | Boundary assignment rests on stated assumptions: 11 KTV loads with unlisted neighbours default to KTV-VSKP, and 129 entries / 114 exits are gap-filled. The KK-line `via` list needs an SME check |
| F13 | #46 | Medium | Twin rules not yet enforced (M1 backlog): the crossing standing-room (CSR) rule is counted (`csr_exceeded`) but not enforced; FR-TWIN-10 in full, the rule auditor (FR-VER-12), the observation model (FR-TWIN-13), the injector and station-master agents (FR-TWIN-15, FR-SCN-02) are absent. The reversible-line direction gate is in the twin but not in the IR |
| F14 | #46 | Low | No stochastic perturbation yet, so the seed only seeds event ids (FR-TWIN-11). `gt.state` is sampled every 60 s, the spec target is 1 s (`twin.sim.state_period_s`) |
| F15 | #46 | Low | BL-0 gives long passenger delays on 20 Aug 2025 (mean about 18 min, maximum about 2.9 h) as trains queue first come first served. It is the do-nothing baseline; compare with the historical delays before using it as a KPI reference |
| F16 | #47 | Medium | DISPLIB-style IR: check field names and occupancy semantics against `displib_spec_2025-09-17.pdf` before P2 (FR-TWIN-19); only trains entering in a window are converted, so rolling windows need frozen decisions for trains already running |
| F17 | #45 | Low | Data fidelity: HCM is a halt flagged as a crossing facility in the COA data (excluded from the crossing rule); 1,029 freight path legs in the log name blocks that skip stations (kept as observed) |
| F18 | #23 | Low | M1 frontend and observability: DR-0001 §3.3 conditions (pinned chart version, separate chunk, About page, npm licence gate), the OSM map attribution, and Grafana per DR-0001 §4 |

## 5. Open risks for M1

| Risk | Note |
|---|---|
| Passenger running-time fidelity (F01) | Booked-time adherence in the twin and the optimiser's run-time minima depend on it |
| Solver latency at KTV–PSA scale (RK-06, OD-33) | The M1 latency spike decides full section vs the VZM–PSA fallback |
| DISPLIB licence (OD-17) | The request to SINTEF is drafted but **not sent** ([status](../licensing/displib-licence-request.md): "drafted, awaiting user to send"). Until it is answered, use stays internal and nothing is benchmarked publicly |
| Synthetic infrastructure | Gradients come from a surface DEM, curves from OSM, and signals and sub-blocks are synthetic (1 km spacing is DUMMY). The calibration supplements absorb some of their errors |
| Version governance (F05, F06) | Parameter edits silently change published content |
| Scenario library v0 (#21) | Not started; needs F10 and F11 |

### Open decisions (spec §15, status Open)

These remain open on 2026-11-08:
- OD-03 offline pulls;
- OD-04 precedence and weights;
- OD-05 rule-catalogue owner;
- OD-07 meaning of "execute";
- OD-08 crew and HOER;
- OD-10 language;
- OD-12 delay-cause codes;
- OD-13 graded punctuality;
- OD-14 evaluation protocol;
- OD-15 LLM hosting;
- OD-17 DISPLIB licence;
- OD-18 solver licensing;
- OD-20 regulatory posture;
- OD-21 railML;
- OD-25 planning horizon;
- OD-26 graded autonomy;
- OD-27 two-person rule;
- OD-28 demo targets;
- OD-33 MVP extent.

Before M1 work starts, the ones that need a decision are:
- OD-04 and OD-05, for the verifier and rule catalogue;
- OD-25, for the planning horizon;
- OD-33, after the latency spike.

## 6. Consequences

- When this PR is merged, M0 closes with the DSC-10 passenger shortfall accepted by the owner; F01 stays open as the M1 follow-up.
- F01 to F18 are drafted as M1 issues in the PR description of #48 (F01 to F12) and in the description of #49 (F13 to F18). The project owner creates them; they are not created by these PRs.
- Spec upkeep for the spec owner: OD-16 closed (option A, DR-0001 D1), OD-17 request drafted and awaiting send, DSC-10 outcome as in section 3.
