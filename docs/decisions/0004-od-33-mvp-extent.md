# DR-0004: MVP extent, full KTV-PSA section or the VZM-PSA fallback (OD-33)

| Field | Value |
|---|---|
| Spec references | §15 OD-33; REC-02, REC-30; RK-06; NFR-SCL-03, NFR-PERF-01..03; §12.3 latency spike; DR-0002 F19 |
| Date | 2026-11-10 |
| Status | **Proposed** (accepted when the PR that adds it is merged) |
| Supersedes | n/a |

## Context

REC-02 makes the full KTV-PSA section (22 stations, 176.83 km, with automatic block on KTV-VZM and
absolute block on VZM-PSA) the MVP. The fallback is VZM-PSA (18 stations, absolute block only) if the M2
latency targets cannot be met on the full section. OD-33 left the choice to an M1 CP-SAT latency spike
(issue #76).

## Evidence

[`docs/optimiser/cpsat-latency-spike.md`](../optimiser/cpsat-latency-spike.md) reports CP-SAT
(`raildss_optimiser.cpsat`) on full-section IR windows of 20, 40 and 70 trains of the real day
2025-08-20. Each window was run warm (BL-0 hint) and cold, within the routine (15 s) and disruption (60 s)
limits, and every solution was verified by G1.

- The busiest 2 h of the real day has **23 trains entering**. A 120 min horizon (OD-25) holds about
  20-40 trains.
- At 20 and 40 trains, warm CP-SAT gives a verified plan **7-27 % better than BL-0 within 15 s**. The
  first feasible plan comes at 1.5-3.7 s.
- At 70 trains (the NFR-SCL-03 stress size), the first feasible plan comes at about 8-10 s, inside the
  Tier 2 budget of 30 s P95, but the plan improves on BL-0 by only about 1 % in 60 s.

## Options

1. **Full section.** One model covers both block systems, as in the demo story (REC-02, REC-30). The risk
   is solver latency at stress sizes.
2. **VZM-PSA fallback.** A smaller model and simpler signalling. It drops the automatic-block stretch
   from M2, which then moves to M3.

## Decision

**Option 1: the full KTV-PSA section is the MVP extent.** The VZM-PSA fallback is not taken.

Conditions:
- The optimiser always runs **warm-started from the greedy plan**, and the **greedy plan is the
  fallback** when CP-SAT returns nothing within `opt.time_limit_s` (FR-OPT-02, FR-OPT-03). Cold
  CP-SAT is not used on its own.
- The horizon stays at **H = 120 min with rolling re-plans** (OD-25), which keeps real instances at 20-40
  trains.
- **Re-check at M2** on conflict- and disruption-driven instances on the reference hardware. Take the
  fallback if Tier 2 misses 30 s P95 (NFR-PERF-02) or a disruption misses 60 s P95 (NFR-PERF-03) on the
  scenario library.

## Consequences

- M2 plans for the full section. The automatic-block sub-blocks stay in the model.
- `raildss_optimiser.cpsat` is the CP-SAT baseline for #75 (greedy) and the M2 optimiser work. Its
  solutions are DISPLIB documents that G1 checks.
- The weak LP bound (gap near 100 %) means quality is reported against the greedy and BL-0 plans, not
  as a proved optimality gap.
