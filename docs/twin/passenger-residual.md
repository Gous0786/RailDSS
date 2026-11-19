# Passenger calibration residual (DSC-10 rev. v1.7; issues #59, #137)

This note is for the product owner. Under DR-0005, each passenger stop-to-stop span (at least 3
legs) is scored against **its own train's booked net WTT time**: booked time minus the recorded
engineering and traffic allowances. The tolerance is ±1 min and the target is ≥ 95 % of spans.

**The target is not met: 98 of 307 spans are within ±1 min (31.9 %).** Every span and every miss
is itemised in [runtime-calibration.md](runtime-calibration.md), with a summary per stop pattern.

## Model

- **Physics.** The point-mass model runs each leg in its stopping context. A leg that starts or ends
  at a booked stop leaves from, or is received on, the booked platform's station line at
  `station_line.speed_kmh` (15 km/h loops, 100 km/h main lines; issue #59). The passenger cruise cap
  is 105 km/h, chosen on the physics-only fit.
- **Supplements.** There is one signed supplement per leg, direction and stopping context
  (`pass-pass`, `stop-pass`, `pass-stop` or `stop-stop`).
  - They are fitted jointly on every train's stop-to-stop segments, with a robust (absolute-error)
    loss. The WTT rounds every point to the minute and shifts time between neighbouring legs, so a
    single leg is not a reference.
  - No leg may run faster than its physics at line speed (VER-LSP-01).
  - Nothing is fitted per span or per train.
- **A span is the sum of its legs.** This is also how the twin runs it.

| | v1.5: modal reference (#59) | v1.7: own booked time (#137) |
|---|---:|---:|
| Spans scored | 191 of 307 | 307 of 307 |
| Within ±1 min, calibrated | 52 (27.2 %) | **98 (31.9 %)** |
| Within ±1 min, physics alone | 9 | 73 |
| Same model without the station-line approach | 15 | 115 (37.5 %) |

The station-line approach costs 17 spans. The WTT's allowances do not depend on whether the booked
platform is a 15 km/h loop or a main line (#59), so the book has no loop reception time to match.
The approach stays in the model because it is how the twin moves trains.

## Why 95 % is out of reach

- **Ceiling of any per-leg model.** Suppose a model reproduced exactly, for every leg, direction and
  stopping context, the most common booked leg time. It would put only **160 of 307 spans
  (52.1 %)** within ±1 min of their own booked time. With a separate value per train class (SUF,
  MEX, PAS, MEMU and so on) the ceiling is 164. That ceiling ignores the speed limits.
- **Per-train pathing time.** The rest is time the WTT books for individual trains beyond the
  labelled allowances. Reproducing it would need per-train fitting, which DSC-10 forbids.
- **Variants tried, unbounded.** Per-leg medians, class-specific supplements, a modal estimator and
  other cruise caps all score 133-145 of 307. They do so only by letting some legs run faster than
  the line speed; the greedy dispatcher's G2 check rejects those plans (VER-LSP-01).
- **Variants tried, bounded.** Leg-by-leg medians score 70-80. The joint fit scores 98-100 at every
  cap from 105 to 130 km/h.
- **Residuals are mostly negative.** The robust fit follows the typical train, so trains carrying
  extra booked time appear as negative residuals: the twin is faster than their book.

## CHE-PSA DN (9 legs): the largest pattern

There are 48 spans; their booked net times run from 2,490 to 5,100 s. Three are within ±1 min.

- **The spread is on one leg.** It sits almost entirely on PUN→PSA (12.29 km). Trains such as 22842,
  22860 and 22884 are booked 1,140 s net there; 12515, 12509 and 22643 are booked 540 s. Their legs
  from CHE to PUN are identical (2,040 s).
- **PSA is an interchange point.** The WTT flags PSA `INTERCHANGEPOINT = 1` for every train in both
  directions, and no other station of the section. Public sources describe Palasa as the boundary
  station between the Waltair (Visakhapatnam) and Khurda Road divisions; a later Gazette
  realignment moves the Palasa-Ichchapuram section to the Visakhapatnam division. Time booked before
  a divisional interchange is consistent with recovery time for an on-time handover.
- **Booking is irregular.** The WTT books this time partly as labelled allowances at PUN (eng
  120-1,020 s, traffic 120-780 s) and, for some trains, as unlabelled running time.
- **The UP direction has less of it.** PSA-CHE UP spans range 2,580-3,660 s.
- **The PSA platform is a minor effect.** DN stoppers use PF-3, which is station line 6 (DNML5). It
  is recorded as a 15 km/h loop in the estimated PSA line data (OD-31), although the name suggests a
  main line.

## Decision requested

Accept **98 / 307 (31.9 %)** as the documented M1 passenger result under DSC-10 v1.7. The ceiling
of any per-leg model is 52.1 %, even before the speed limits. Reaching 95 % would need per-train
running-time data or per-train fitting.

Optionally, drop the station-line approach from the scored model. That gives 115 / 307 (37.5 %),
at the cost of the twin no longer slowing trains on loop platforms.
