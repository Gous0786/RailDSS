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
are noted as "probed". Defects become M1 follow-ups F-xx (section 6). Nothing is fixed here.

| PR | Scope | Findings |
|---|---|---|
| #34 | WTT import and dated expansion (#10) | **No defect found.** Checked: the Monday-first masks; `DAY_OF_RUN` and `BLOCK_BUSY_DAYS` (DQ-19 passes on all 4,260 rows); `runs_on` origin-date logic. Probed: `runs_on` takes 0.15 s per day (not a performance risk) |
| #36 | Freight demand and statistics (#11) | **No defect found.** Checked: the block times are recomputed from timestamps (DQ-21); the loads per day agree with R06 (within 7 %); every wagon code in the log maps to a rolling-stock family (`unmapped = []`). Observation: per-load first and last rows rely on log order, which DQ-21 guarantees |
| #42 | Boundary flows, review fix round (#12) | **F11, F12.** The tamper tests for both cross-checks are sound. Probed: no freight exit falls outside the 27 entry days |
| #43 | Running-time calibration (#16) | **F01, F02, F03, F04** |
| #44 | DQ report and CI gate (#17) | **F05** |
| #45 | Network publication (#18) | **F06, F07, F08, F09.** Probed: all 50 blocks have gradient records today, so F07 is a missing guard, not missing data |
| #46 | Twin skeleton and direction-gate fix (#20) | **Direction gate: no defect found.** Checked: claims and releases balance on every path (immediate grant, wait then grant, woken by the gate then retry); the sim interrupts no process, so a claim cannot leak; the new tests cover the same-instant race, queue retention and the deadlock false positive. **Twin: F10** |

Each finding F01 to F12 is an item of the consolidated carry-over list in section 6, which also holds the items
that came from the twin and converter work (F13 to F21).

## 5. Open risks for M1

| Risk | Note |
|---|---|
| Passenger running-time fidelity (F01) | Booked-time adherence in the twin and the optimiser's run-time minima depend on it |
| Solver latency at KTV–PSA scale (RK-06, OD-33, F19) | The M1 latency spike decides full section vs the VZM–PSA fallback |
| DISPLIB licence (OD-17, F21) | The request to SINTEF is drafted but **not sent** ([status](../licensing/displib-licence-request.md): "drafted, awaiting user to send"). Until it is answered, use stays internal and nothing is benchmarked publicly |
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

## 6. Consolidated M1 carry-over

One numbered list. It merges the twelve follow-ups drafted in the re-check (F01 to F12, PR #48) with the items from
the twin and converter work and from the M0 backlog's "left for M1" list. Overlaps are merged (the notes name them).
The project owner creates the issues; none were created by the PRs that carry this record. Items are grouped by area;
order within a group is not a priority.

### Calibration and running times

**F01 Passenger calibration: station-approach model** (high, #16). Merges the SME flag and the "itemise misses" item.
- **Problem:** 27.8 % of unpadded WTT spans are within ±1 min, against 95 %. Trains stopping at PSA, CHE and VZM are
  6 to 9 min too fast. The report lists only the ten worst spans, all CHE-PSA (9 legs, down), so the other 128 scored
  misses are not itemised.
- **Scope:** model reception and departure through the station line (turnout and loop speeds from
  `station_line.speed_kmh` and the line choice); fit supplements only as the approved definition allows (no per-span
  fitting); itemise every scored miss by span in the report; get the SME answer on what the 3,180 s CHE-PSA time
  contains beyond running time.
- **AC:** passenger at least 95 % of unpadded spans, or a documented residual with an SME sign-off.

**F02 Freight calibration per wagon family** (medium, #16).
- **Problem:** one reference consist (BOXN) stands for every wagon type, while the log p10 mixes types.
- **Scope:** per-family templates (#15) weighted by each block's wagon mix, or p10 per family where n is at least 30.
- **AC:** freight still at least 90 %, and the report shows the mix used per block.

**F03 Route-aware PSRs in the running-time track** (medium, #16).
- **Problem:** every PSR of a direction applies to every train, ignoring the PSR's lines (for example 5440, 15 km/h over
  the VZM diamond, lines DN/MID).
- **Scope:** apply a PSR only to trains on its lines.
- **AC:** a unit test with a PSR on MID does not slow a DN-main run; the calibration is re-run and reported.

**F04 Honest reproducibility check in the calibration report** (low, #16).
- **Problem:** `CalibrationReport.reproducible` is an in-process probe on one block and over-states the guarantee.
- **Scope:** replace it with a recorded expected-seconds fixture, or drop the field.
- **AC:** the check fails when the physics changes.

### Data pipeline, publication and governance

**F05 Decouple the DQ gate from the register hash** (medium, #17).
- **Problem:** the DQ baseline contains the register content hash, so any unrelated parameter edit fails `dq --check`
  (it did in #43).
- **Scope:** keep `register_version` out of the diffed content and record it separately.
- **AC:** an unrelated parameter edit does not fail `dq --check`; a DQ fact change still does.

**F06 Version governance for the published network** (medium, #18). Includes the "two refreshes after a parameter edit" item.
- **Problem:** `network_version.json` embeds the DQ report with the register hash, so a parameter edit changes the hash of
  `KTV-PSA-2025-08` without a version bump, and `publish --check` forces a regeneration under the same label. Any edit
  to the register or the inputs now needs both `dq --update-baseline` and `raildss network publish`.
- **Scope:** decide that a content change means a new `network_version`, or keep non-network metadata (DQ report,
  register hash) out of the network hash.
- **AC:** a parameter edit that does not touch network content leaves the hash unchanged; a content change is refused
  under the same label.

**F07 Validator coverage rules** (low, #18).
- **Problem:** nothing enforces that every COA block has gradients and every block line a speed (true today).
- **Scope:** add the rule to the validator.
- **AC:** removing one gradient record fails the rule.

**F08 Version stamps for static tables** (low, #18, FR-DATA-04).
- **Problem:** N-05 checks `network_version` and `timetable_version` only; `policy_version` and `rule_catalogue_version`
  are unstamped by design (provenance card), and FR-DATA-04 does not say static tables are exempt.
- **Scope:** stamp them, or write the exemption into FR-DATA-04.
- **AC:** N-05 enforces whichever is chosen.

**F09 Publish `train_run` and the rolling-stock tables** (medium, #18).
- **Problem:** `train_run` (the dated WTT expansion) and `loco_class`, `vehicle_type`, `consist` (#15) are empty in the
  publication; the twin rebuilds them in memory.
- **Scope:** add them to the publication.
- **AC:** `raildss network show` counts are above 0, and the twin reads them from the publication.

**F10 Twin support for paths that start or end at a halt** (medium, #20).
- **Problem:** train 22501 on 2025-08-20 is rejected, so a "real day" is not complete; trains already running at a
  window start have no position (see F16).
- **Scope:** support such trains in the plans.
- **AC:** the day run has no UNSUPPORTED trains on the 27 freight days.

**F11 Weekday and date-range acceptance windows** (low, #12).
- **Problem:** acceptance windows are daily only, which limits boundary-closure scenarios.
- **Scope:** extend `acceptance_windows` with weekdays and validity dates, validated for overlap.
- **AC:** a boundary-closure scenario can be expressed.

**F12 SME check of boundary assumptions** (low, #12).
- **Problem:** 11 KTV loads with unlisted neighbours default to KTV-VSKP; 129 entries and 114 exits are gap-filled; the
  KK-line `via` list is unchecked.
- **Scope:** an SME reviews the lists and rules.
- **AC:** reviewed, with any changes recorded in `config/boundaries.yaml`.

**F17 Data-fidelity notes from publication** (low, #18).
- **Problem:** HCM is a halt flagged as a crossing facility in the COA data (excluded from the crossing rule);
  1,029 freight path legs in the log name blocks that skip stations (kept as observed).
- **Scope:** confirm HCM with the data owner; decide whether skipping legs are expanded to the stations between.
- **AC:** each recorded as confirmed or corrected in the mapping spec.

### Twin and solver

**F13 Twin rule enforcement and the rest of the twin** (medium, #20, M0 backlog "left for M1").
- **Problem:** the crossing standing-room (CSR) rule is counted (`csr_exceeded`) but not enforced; FR-TWIN-10 in full,
  the rule auditor (FR-VER-12), the observation model and degradation profiles (FR-TWIN-13), the injector and
  station-master agents (FR-TWIN-15, FR-SCN-02) are absent. The reversible-line direction gate is in the twin but not
  in the IR.
- **Scope:** implement them as planned for M1.
- **AC:** 30 seeded nominal days with 0 violations by the rule auditor (FR-TWIN-10); CSR violations fail rather than count.

**F14 Perturbation and ground-truth resolution** (low, #20).
- **Problem:** no stochastic perturbation, so the seed only seeds event ids (FR-TWIN-11); `gt.state` is sampled every
  60 s against the 1 s target (`twin.sim.state_period_s`).
- **Scope:** add the perturbations; measure the cost of 1 s state.
- **AC:** FR-TWIN-11 checks pass; the state period is justified by a measured run time.

**F15 BL-0 as a KPI reference** (low, #20).
- **Problem:** BL-0 gives long passenger delays on 20 Aug 2025 (mean about 18 min, maximum about 2.9 h) as trains queue
  first come first served.
- **Scope:** compare with the historical delays of that day before BL-0 is used as the do-nothing reference.
- **AC:** the comparison is in the KPI notes with the gap explained or accepted.

**F16 DISPLIB-style IR: spec check and rolling windows** (medium, #22).
- **Problem:** the field names and occupancy semantics follow the published description as recalled; only trains entering
  in a window are converted, so trains already running need frozen decisions.
- **Scope:** check against `displib_spec_2025-09-17.pdf` before P2 (FR-TWIN-19); add frozen decisions for rolling windows.
- **AC:** a spec cross-check note; a window starting mid-day converts and the evaluator accepts the twin's schedule for it.

**F19 CP-SAT latency spike on the full section** (high, §12.3, RK-06, OD-33).
- **Problem:** whether the solver meets the latency target at KTV-PSA scale is unknown; the result decides full section
  against the VZM-PSA fallback.
- **Scope:** run CP-SAT on IR instances (#22) of growing windows up to the full section.
- **AC:** time-to-quality results published and the OD-33 decision recorded.

**F20 Scenario library v0** (#21, M1).
- **Problem:** not started; needs F10 and F11.
- **Scope:** as in #21.
- **AC:** 10 scenarios, both real days running.

### Licence, frontend and spec

**F18 M1 frontend and observability** (low, #23).
- **Scope:** the DR-0001 section 3.3 conditions (pinned chart version, separate chunk, About page, npm licence gate), the
  OSM map attribution, and Grafana per DR-0001 section 4.
- **AC:** the licence gate passes and the conditions are checked in CI.

**F21 Owner and spec-owner actions** (low).
- **Problem:** the SINTEF DISPLIB request is still "drafted, awaiting user to send"; spec section 15 does not yet show
  OD-16 closed (option A, DR-0001 D1) or OD-17 awaiting send; DSC-10 outcome as in section 3.
- **Scope:** the owner sends or drops the request and records the status; the spec owner updates section 15.
- **AC:** the request status table and the spec agree.

## 7. Consequences

- When this PR is merged, M0 closes with the DSC-10 passenger shortfall accepted by the owner; F01 stays open as the
  M1 follow-up.
- Section 6 is the single carry-over list; PR #48's draft list is superseded by it (same F-numbers for F01 to F12).
