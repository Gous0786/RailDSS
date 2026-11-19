# DR-0005: DSC-10 passenger KPI against each train's own booked time

| Field | Value |
|---|---|
| Spec references | §12 DSC-10 (rev. v1.5 → v1.7); FR-TWIN-06; DR-0002 F01 |
| Issues | #137 (this change), #59 (station-approach model, PR #136), #16 |
| Date | 2026-11-19 |
| Status | **Accepted** (PR merged) |
| Supersedes | The v1.5 passenger criterion of DSC-10 (modal reference per stop pattern) |

## Context

DSC-10 v1.5 scored each passenger stop-to-stop span against one reference per stop pattern: the
modal net WTT time. Spans more than 1 min above it were classed as padded and only reported.

Issue #59 showed that this reference cannot reach the 95 % target. Even a model that reproduced
each train's own booked net time would put only 163 of 191 scored spans (85.3 %) within ±1 min of
the modal value. Trains with the same stops differ by up to 11 min net of the recorded allowances.
For example, CHE-PSA DN carries 600 s of unlabelled time on PUN-PSA in some trains and not in
others. See [passenger-residual.md](../twin/passenger-residual.md).

## Options

1. **Per-train reference.** Score each span against its own train's booked net WTT time.
2. Keep the modal reference and accept the documented residual.

## Decision

**Option 1, decided by the user on 2026-11-19.**

- Each passenger stop-to-stop span of at least 3 legs is scored against **its own train's booked net
  WTT time**, net of the recorded engineering and traffic allowances.
- The tolerance stays at **±1 min** and the target at **≥ 95 %** of spans.
- The padded category is dropped: every span is scored.
- Freight is unchanged.

Model rules that follow from the decision (issue #137):

- A span is simulated as the **sum of its legs**, which is how the twin runs it. Each leg is physics
  in its stopping context (from or to a booked stop, on the booked platform's station line, #59)
  plus one supplement per leg, direction and stopping context.
- The supplements are **fitted jointly on every train's stop-to-stop segments**, not leg by leg. The
  WTT rounds every point to the minute, which shifts time between neighbouring legs: KBM-DGB is
  booked 180 s for 7.98 km (160 km/h) while its neighbour is booked long. The fit uses a robust
  (absolute-error) loss, so trains carrying extra booked time do not pull every supplement up.
- A supplement is **signed** but bounded: no leg may run faster than its physics at line speed,
  because otherwise the twin breaks the speed limits (VER-LSP-01).
- Supplements are never fitted per span or per train.
- As for freight, the passenger cruise cap is chosen on the physics-only fit (105 km/h). Signed
  supplements make the calibrated share almost flat in the cap, so the cap cannot be chosen on it.

## Result

| | v1.5 (modal reference) | v1.7 (own booked time) |
|---|---:|---:|
| Spans scored | 191 (116 padded, not scored) | 307 (all) |
| Within ±1 min, calibrated | 52 (27.2 %) | **98 (31.9 %)** |
| Within ±1 min, physics alone | 9 | 73 |
| Same, without the station-line approach (#59) | 15 | 115 (37.5 %) |
| Freight blocks within ±15 % | 59 / 64 | 59 / 64 (92.2 %) |

**The 95 % passenger target is still not met.** The remaining residual is per-train pathing time.
Suppose a model reproduced exactly, for every leg, direction and stopping context, the **most
common** booked leg time. It would put only **160 of 307 spans (52.1 %)** within ±1 min of their
own booked time, or 164 with a separate value per train class. That ceiling also ignores the speed
limits. The rest is time the WTT books for individual trains, for example PUN-PSA at 1,140 s for
some trains and 540 s for others. A per-leg model cannot reproduce it without per-train fitting,
which DSC-10 forbids.

**Two findings for the product owner:**

- **Without the speed-limit bound** the per-leg median scored 142 (46.2 %), but only by letting legs
  run faster than the line speed.
- **The station-line approach costs 17 spans** (98 against 115). The WTT's allowances do not depend
  on whether the booked platform is a 15 km/h loop or a main line (#59). The approach stays in the
  model because it is how the twin moves trains.

## Consequences

- The calibration report scores and itemises all 307 spans against their own booked time
  ([runtime-calibration.md](../twin/runtime-calibration.md)).
- The twin's passenger leg times now use the stopping-context supplements and the 105 km/h cap, and
  never beat its physics at line speed.
- A calibrated leg is never faster than the same leg with fewer stops, so stopping cannot shorten a
  run. The DISPLIB IR minimum durations and the CP-SAT warm start rely on this.
- Three simulator and planner fixes, all exposed by the new timings, make replans reproduce the frozen
  plan (VER-FRZ-01):
  - A plan hold shorter than `twin.sim.hold_stand_s` (1 s) no longer makes a train stand; such a
    hold only rounds a recorded decision time.
  - A held train joins the queue for its block line that long before the hold, and leaves at the
    hold.
  - The greedy plan exports a hold for every wait it imposes. This includes a train standing at a
    station it would pass, and a train still inside the section at the end of the horizon.
- The scenario hashes, the BL-0 note, the chart data and the v0 scenario KPIs were regenerated.
- The shortfall against 95 % stays documented for product-owner review. Reaching 95 % would need
  per-train timing data, which is out of scope under DSC-10.
