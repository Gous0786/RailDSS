# RailDSS documentation

Start with the [project README](../README.md) for setup, one-command runs and status. Then read
[architecture.md](architecture.md) for the pipeline, package map, import boundaries and data flow.
The published network is `KTV-PSA-2025-08-r2`.

## Decision records ([index](decisions/README.md))

| Record | Subject |
|---|---|
| [DR-0001](decisions/0001-licence-review-m0.md) | M0 licence review (train-graph component, Grafana, DEM/OSM, DISPLIB) |
| [DR-0002](decisions/0002-m0-exit-review.md) | M0 exit review and M1 carry-over |
| [DR-0003](decisions/0003-od-04-05-25-policy-rules-horizon.md) | Precedence policy, rule-catalogue ownership, planning horizon (OD-04, OD-05, OD-25) |
| [DR-0004](decisions/0004-od-33-mvp-extent.md) | MVP extent: the full KTV-PSA section, not the VZM-PSA fallback (OD-33, REC-02) |
| [DR-0005](decisions/0005-dsc10-passenger-per-train-reference.md) | DSC-10 passenger KPI against each train's own booked time |
| [DR-0006](decisions/0006-network-version-governance.md) | Network version governance (content hash; a content change needs a new label) |
| [DR-0007](decisions/0007-static-table-version-stamps.md) | Version stamps of static tables |
| [DR-0008](decisions/0008-m1-exit-review.md) | **M1 exit review**: criteria evidence, deviations, M2 carry-over |

## KPIs and evaluation

- [Closed-loop replay of 2025-08-20](kpi/replay-ktv-psa-2025-08-20.md): DSS against BL-0, the M1 demo.
- [KPIs of the v0 scenarios](kpi/scenarios-v0.md): BL-0 and the decision loop.
- [Historical reference KPIs](kpi/historical-reference-ktv-psa.md) (BL-5, from the freight log), with
  its [JSON](kpi/historical-reference-ktv-psa.json).
- [BL-0 against the historical day](kpi/bl0-reference-check-2025-08-20.md): the K1 reference check.
- [ETA accuracy on held-out real freight days](dss/eta-evaluation.md).

## Twin and calibration

- [Running-time calibration report](twin/runtime-calibration.md) (DSC-10) and the
  [passenger residual](twin/passenger-residual.md).
- [Solver-facing IR, DISPLIB-style](twin/displib-ir.md) and the [DISPLIB cross-check](twin/displib-crosscheck.md).
- Scenarios: [`scenarios/v0`](../scenarios/v0/INDEX.md) (10 scenarios, executable in the twin).

## Reviews

- [M1 face-validity review](review/m1-face-validity.md) (historical chart comparison, FV-01 to FV-06).

## DSS, optimiser and orchestrator

- [Decision orchestrator](dss/orchestrator.md) (DS-9, the rolling loop).
- [CP-SAT latency spike](optimiser/cpsat-latency-spike.md) on the full section (the evidence for DR-0004).

## Data and ETL

- [KTV-PSA mapping](etl/mapping-ktv-psa.md): COA master tables to the canonical schema.
- [Data-quality baseline](etl/dq/ktv-psa-baseline.md) (DQ-14 to DQ-22).
- [Boundary review](etl/boundary-review-ktv-psa.md).
- [Dummy-data catalogue](data/dummy-data-catalogue.md) and [dummy parameter values](params/dummy-values.md).

## Licensing

- [DISPLIB licence request](licensing/displib-licence-request.md) (OD-17).
