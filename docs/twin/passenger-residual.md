# Passenger calibration residual (issue #59, DSC-10)

For product-owner acceptance. The DSC-10 passenger target (at least 95 % of unpadded WTT spans within
+-1 min) is **not met**: 52 of 191 scored spans (27.2 %). This note shows why the target cannot be met
under the approved definition, what the station-approach model changed, and why the CHE-PSA DN span is
booked 3,180 s. Every scored miss is itemised in [runtime-calibration.md](runtime-calibration.md),
with a per-stop-pattern summary.

Approved definition (unchanged): the reference is the modal net WTT time of each stop pattern; spans
more than 1 min above it are padded and reported, not scored. The model is physics plus per-leg,
per-direction supplements fitted on unpadded non-stop legs. There is no per-span fitting.

## What changed

| | Before (#16 / #60) | After (#59) |
|---|---:|---:|
| Passenger cap | 130 km/h | 120 km/h |
| Scored spans within +-1 min | 53 / 191 | 52 / 191 |
| Same model, no station-line approach | - | 15 / 191 |
| Freight blocks within +-15 % | 59 / 64 | 59 / 64 (unchanged) |

1. **Station-approach model.** A stop-to-stop run leaves from, and is received on, the station line of
   its booked platform (WTT planned line, mapped through the `platform` table to `station_line`). The
   speed comes from `station_line.speed_kmh`: 15 km/h on loops, 100 km/h on main lines.
   - Reception: the train runs at the line speed over the line's CSR before it stops.
   - Departure: the train holds the line speed until its rear clears the exit turnout.
   The twin uses the same rule through `Timing.leg`, so the simulator and the calibration agree.
2. **Supplements need a sample.** A leg is supplemented only when at least
   `runtime.calibration.supplement_min_legs` (30) non-stop runs cross it. Before, NML-VZM UP got
   +277 s from 5 trains running through VZM, where almost every passenger train stops. That pushed
   every CHE-VZM UP span about 3 min too slow.

Change 2 alone gives 15 / 191. The loop-speed reception and departure recover the time that the
small-sample supplements had absorbed. The result is a more physical model at the same score.

## Why 95 % is out of reach

- **The WTT disagrees with its own reference.** Suppose a model reproduced every train's own booked
  net time exactly. It would still put only **163 of 191** scored spans (85.3 %) within +-1 min of
  the modal reference. One reference per stop pattern cannot match trains whose net times differ by
  up to 11 min. Most of this is CHE-PSA DN, where 20 of 27 scored spans are booked more than 1 min
  below the reference, by as much as 690 s.
- **Physics spreads where the WTT does not.** One stop pattern carries Vande Bharat (8 coaches) to
  MEMU and 24-coach mail trains. The WTT books them with class allowances, not a running-time
  calculation. Even with the best constant shift per stop pattern (that is, per-pattern fitting,
  which the definition forbids), only 174 / 191 would fall within.
- **The WTT does not book loop reception.** Its acceleration and deceleration allowances
  (`acc_s` / `dec_s`) depend only on train class: SUF 120 / 60 s, PAS and MEMU 60 / 120 s,
  Vande Bharat 30 s. They are the same whether the booked platform is a 15 km/h loop or a main
  line, for example SUF: 120 s at 81 of 98 loop stops and at 181 of 189 main-line stops. So the
  physically correct approach model cannot, by itself, raise agreement with the WTT.

## CHE-PSA DN (9 legs): booked 3,180 s, twin about 2,700 s

- **The gap is one leg.** Leg by leg, the reference trains (22842, 22860, 22884) and the fast ones
  (12515, 12509, 22643) are booked the same from CHE to PUN, 2,040 s. They differ only on PUN to PSA
  (12.29 km): 1,140 s net for the reference trains against 540 s, a 600 s difference. The reference
  3,180 s is the mode of only 5 of 48 spans. The scored net times run from 2,490 s (20842) to 3,240 s.
- **It is an interchange approach.** The WTT marks PSA as an interchange point
  (`INTERCHANGEPOINT = 1`) for every train in both directions, and for no other station of the
  section. Public sources describe Palasa as the boundary station between the Waltair
  (Visakhapatnam) and Khurda Road divisions of East Coast Railway; a later Gazette realignment
  moves the Palasa-Ichchapuram section to the Visakhapatnam division. Time booked just before a
  divisional interchange is consistent with recovery time for an on-time handover. The WTT books it
  partly as labelled allowances at PUN (eng 120-1,020 s, traffic 120-780 s) and, for some trains,
  as unlabelled running time on PUN-PSA. Only the labelled part is netted out.
- **It is one-way.** UP trains leaving PSA have no such time. PSA-CHE UP is booked 2,700 s, and the
  twin is within 1 min on 22 of its 24 spans.
- **The platform is a minor part.** DN stoppers are booked on PSA PF-3, which is station line 6
  (DNML5). It is recorded as a 15 km/h loop in the estimated PSA line data (spec OD-31). Reception
  there adds about 135 s, not 600 s. The name DNML5 suggests a main line, so this effect may even be
  overstated.

The remaining CHE-PSA residual (about -480 s) is WTT pathing time, not running time. Fitting it would
take a per-span or per-station-pair supplement, which the approved definition excludes (OD-05: no
SME owner).

## Other large residuals (itemised in the report)

- **VZM-KTV UP (-314 s) and the long VZM-PSA / PSA-VZM spans (about -370 s):** the twin is faster
  than booked. The 17-leg spans include the PSA interchange approach above. VZM-KTV ends at the
  section boundary; 7 of its 18 scored spans are booked more than 1 min below their own reference.
  It is not analysed further here.
- **VZM-CHE DN (+101 s), CHE-NWP DN (+242 s) and CPP-CHE DN (+93 s):** the twin is slower than
  booked. These spans include CPP DN (PF-1, a 390 m loop) or NWP DN (station line 2) receptions and
  leg supplements fitted on through trains.

## Decision requested

Accept 52 / 191 (27.2 %) as the documented M1 passenger residual, because the 85.3 % ceiling shows
the 95 % target is unattainable under the approved definition. Alternatively, revise DSC-10 so a
span is scored against its own train's booked net time instead of the modal reference. The report
already prints this comparison ("against every span, standard or padded").
