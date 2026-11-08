# DR-0003: Precedence policy, rule-catalogue ownership and planning horizon (OD-04, OD-05, OD-25)

| Field | Value |
|---|---|
| Spec references | §15 OD-04, OD-05, OD-25; REC-09, REC-12; FR-DATA-02, FR-OPT-07, §9.2; RK-02, RK-17 |
| Date | 2026-11-08 |
| Status | **Accepted** by the product owner on 2026-11-08, with the recommended defaults |
| Supersedes | n/a |

## Context

The M0 exit review ([DR-0002](0002-m0-exit-review.md) §5) listed OD-04, OD-05 and OD-25 as decisions
needed before M1 work on the verifier, the rule catalogue and the optimiser starts. Spec §15 gave a
recommended default for each, and the product owner accepted all three.

## Decisions

| OD | Decision | Register keys |
|---|---|---|
| OD-04 Precedence order, weights, hard rules, visibility | **Weights** premium 10, mail/express 5, passenger 3, time-tabled freight 2, other freight 1, with the `TRAIN_TYPE` class map. **Relief trains** are a hard top rule, above every weighted class. **Explanations show the weights** they used | `policy.weights`, `policy.class_map`, `policy.hard_rules`, `policy.weights_in_explanations` |
| OD-05 Rule-catalogue owner and validator | **No SME rule owner.** The RailDSS team maintains the catalogue. Rule fidelity is per public sources (G&SR text and the COA data). Rules whose facts are unverified [U] stay at **WARN** and never FAIL | `rules.unverified_max_severity` |
| OD-25 Planning horizon and cadence | **H = 120 min** (configurable 60–180). **Re-plan on trigger events** (new conflict, disruption, controller action, order decline) and at least every **5 min**. **10 min freeze** | `cd.horizon_min`, `opt.replan_triggers`, `opt.replan_max_interval_min`, `opt.freeze_window_min` |

The keys that existed already keep their values. Their `source` and `notes` now cite this record. The new
keys are `policy.weights_in_explanations`, `rules.unverified_max_severity` and `opt.replan_triggers`.
`policy.hard_rules` changes from status U to SA: relief-first is now a product-owner policy, while the
IR practice behind it remains unverified (R01 §2.2).

## Consequences

- The verifier (G2) and the rule catalogue (M1) cap [U] rules at WARN, and the catalogue cites public
  sources only. There is no "approved by rule owner" step; the spec's rule-owner persona becomes the
  RailDSS team.
- The optimiser, conflict detection and the decision orchestrator read the horizon, the triggers, the
  cadence and the freeze from the register.
- Explanations (cards and drawer) list the class weights and any hard rule applied.
- The spec moves to v1.6 (§15 and the sections that reference these ODs).
- Editing the register changed the DQ baseline and the network hash with no change to network content,
  so both were refreshed in this PR. This is the F06 / F05 issue recorded in DR-0002.
