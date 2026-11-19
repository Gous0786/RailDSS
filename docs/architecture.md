# RailDSS architecture (as built at the end of M1)

RailDSS is an **advisory, non-SIL** decision-support system for an Indian Railways section controller.
It comes with a section-level digital twin that stands in for the real railway. The twin produces the
ground truth and the degraded feeds the DSS reads. The DSS detects conflicts, plans, verifies and
recommends. The controller (or, in headless evaluation, an auto-accept policy) decides, and the executor
sends control orders back to the twin's station masters. Interlocking and Kavach remain the safety
layer. RailDSS never sets routes.

The demo section is **KTV-PSA** (Kottavalasa Jn - Palasa, 176.83 km, 22 stations). The published
network is **`KTV-PSA-2025-08-r2`** (`data/published/`), versioned under DR-0006.

## Pipeline

```
data/raw (COA, WTT, freight log)
   │  raildss_etl: ETL, DQ (DQ-14..22), validator (N-01..08), publication (DR-0006)
   ▼
data/published/KTV-PSA-2025-08-r2  ──────────────────────────────┐
   │                                                             │
   ▼                                                             │
raildss_twin  (SimPy meso model, BL-0 dispatching, perturbations, injector, station masters)
   │  gt.*  (ground truth: evaluation only)                      │
   │  obs.* (observation model, profiles D0-D3)  ops.* (disruptions, TSR, blocks)
   ▼                                                             │
raildss_dss   DS-1 ingest + DQ ─► DS-3 state ─► DS-4 prediction ─► DS-5 conflict detection
   │                                                             │
   ▼                                                             │
raildss_optimiser  greedy rolling dispatcher (+ warm-started CP-SAT on the DISPLIB-style IR)
   │                                                             │
   ▼                                                             │
raildss_verifier   G0 state sanity · G1 solver feasibility · G2 rule catalogue
   │               (G3: sandbox replay of the option in the twin, raildss_twin.sandbox)
   ▼                                                             │
raildss_hitl       recommendation lifecycle (TLA+-checked) + executor ─► ctrl.orders ─► twin
   │                                                                  station masters ─► ctrl.order_acks
   ▼
KPIs (raildss_dss.kpi, raildss_twin.kpi_eval) · audit (raildss_audit, hash-chained) · web UI (web/)
```

`raildss_orchestrator` runs the **rolling decision loop** (DS-9) on a twin day. On each tick it advances
the DSS chain. When a re-plan is due (a trigger, or the maximum interval) it plans, verifies (G0-G3) and
releases only options with no FAIL (FR-VER-07). It then accepts, executes and re-plans on order
declines. `replay` adds the KPI comparison with BL-0; `scenarios` runs the v0 library; `audit-replay`
re-verifies a recorded recommendation.

### Stages

| Stage | Module | What it does |
|---|---|---|
| ETL and publication | `raildss_etl` (`publish`, `dq`, `validator`, `timetable`, `rolling_stock`) | Raw dataset → canonical entities (schema in `raildss_schema`). DQ report with baseline. Validator 0 errors. Content-hashed publication: a content change needs a new label (DR-0006). The publication includes `train_run` and the rolling-stock templates (#66). |
| Twin | `raildss_twin` (`sim`, `plans`, `calibration`, `running_time`, `observe`, `perturb`, `injector`, `agents`, `sandbox`, `scenario`) | One real day under BL-0 (WTT plus the real freight day). Point-mass running times calibrated against the freight log and the WTT (DSC-10, DR-0005). Seeded perturbations. Scripted disruptions (`ops.disruptions`). Station-master agents. A trace hash makes runs reproducible. |
| Ingestion and state | `raildss_dss.ingest`, `raildss_dss.state` | DS-1 validates and deduplicates the `obs.*` and `ops.*` feeds (DQ, dead-letter topics). DS-3 holds the train state and staleness. |
| Prediction | `raildss_dss.predict` | DS-4 ETAs: empirical quantiles for freight (from the log) and the WTT plus calibration for passenger trains. |
| Conflict detection | `raildss_dss.conflicts` | DS-5 conflict types 1-4, 6 and 7 over the horizon (`cd.horizon_min`). |
| Planning | `raildss_optimiser.greedy`, `.cpsat`, `.pipeline` | A greedy priority dispatcher (policy weights OD-04, 3-line choice, single-line working), re-planned on triggers with a freeze window (FR-OPT-07). CP-SAT is warm-started from the greedy plan on the DISPLIB-style IR (`raildss_twin.displib`). |
| Verification | `raildss_verifier` (`gates`, `engine`, `rules`, `auditor`) | G0 checks state sanity and G1 solver feasibility. G2 is a declarative rule catalogue (`config/rule_catalogue.yaml`) with a 100 % mutation catch. The offline auditor checks twin days. The verifier is independent of the twin, optimiser and ETL. |
| G3 | `raildss_twin.sandbox` | Each option is replayed in the twin with its holds and lines and checked against the baseline. |
| HITL | `raildss_hitl.lifecycle`, `.executor` | Recommendation lifecycle PROPOSED → PRESENTED (or REJECTED_BY_VERIFIER) → SANDBOX / ACCEPTED / REJECTED / EXPIRED / SUPERSEDED → PRE_EXEC_CHECK → ORDER_ISSUED → ACKNOWLEDGED / ORDER_DECLINED → EXECUTING → COMPLETED, model-checked in TLA+ (`specs/tla`). The executor re-verifies before execution, issues per-station control orders and handles acknowledgements and declines. |
| KPIs | `raildss_dss.kpi`, `raildss_twin.kpi_eval`, `raildss_etl.kpi_reference` | The MVP KPI set (A2, A4-A6, B1, B6, C1, C2, F1, ...). BL-0 against the DSS arm. The historical reference from the freight log. |
| Audit | `raildss_audit`, `raildss_orchestrator.auditing` | An append-only, hash-chained audit log (one chain per section and day). Any recommendation can be replayed. |
| UI | `web/` (React and Vite) | The control chart (COA preset), schematic mimic (#87), queue, alerts, KPI strip, recommendation card and drawer, and audit view. It reads the exports in `web/public/data/` (`python -m raildss_twin chart`). |

## Package map

| Package | Role |
|---|---|
| `raildss_common` | Event envelope (`schemas/events/`), Kafka bus, topic catalogue and ACL lint |
| `raildss_schema` | Canonical entities (micro, meso and macro), DDL, migrations, store |
| `raildss_params` | Parameter register `config/parameters.yaml`: every rule value with a source and status; policy |
| `raildss_etl` | ETL, DQ, validator, publication, rolling-stock templates, KPI reference, the `raildss` CLI |
| `raildss_twin` | Digital twin, calibration, observation model, injector, agents, sandbox (G3), scenarios, chart and mimic export |
| `raildss_dss` | DSS online services DS-1 to DS-5 and the KPI engine |
| `raildss_optimiser` | Greedy dispatcher, CP-SAT, planning pipeline |
| `raildss_verifier` | Gates G0-G2, rule catalogue and engine, offline auditor |
| `raildss_hitl` | Recommendation lifecycle and executor |
| `raildss_orchestrator` | Rolling decision loop, replay with KPI report, v0 scenario report, audit replay |
| `raildss_audit` | Hash-chained audit log, verify and export |
| `web` | Controller UI (TypeScript, React, Vite) |

## Import-boundary contracts

The contracts are enforced by import-linter in CI (`uv run lint-imports`, `pyproject.toml`):

1. **The verifier is independent** of the optimiser, twin and ETL (NFR-MNT-02). It reads the published
   network through its own loader (`raildss_verifier.network`).
2. **Shared foundations** (`raildss_common`, `raildss_schema`, `raildss_params`) depend on no
   application package.
3. **DSS services never import the twin** (FR-TWIN-14). They see only `obs.*` and `ops.*`. This also
   covers indirect paths, which is why the rolling-stock templates live in `raildss_etl` (#66).

## Data flow (Kafka topics, `config/kafka/topics.yaml`)

Every message uses the versioned event envelope (spec §6.2). Principals: `twin`, `evaluation`, `dss`,
`admin`. ACLs keep `gt.*` away from the DSS (DQ-12).

| Family | Topics | Producer → consumer |
|---|---|---|
| Ground truth | `gt.state`, `gt.occupancy`, `gt.signal`, `gt.delay` | twin → evaluation only |
| Observations | `obs.position`, `obs.station`, `obs.occupancy`, `obs.block`, `obs.heartbeat` | twin observation model → DSS (DS-1) |
| Operations | `ops.tsr`, `ops.blocks`, `ops.disruptions`, `ops.rakes`, `ops.crew`, `ops.weather` | injector or external → DSS |
| DSS | `dss.state`, `dss.predictions`, `dss.conflicts`, `dss.candidates`, `dss.verification`, `dss.recommendations`, `dss.explanations`, `dss.alerts`, `dss.kpi` | DSS services → UI and audit |
| Control | `ctrl.decisions`, `ctrl.orders`, `ctrl.order_acks` | controller and executor ↔ twin station masters |
| Audit | `audit.log` | orchestrator → audit store |
| Dead letters | `dlq.obs`, `dlq.ops`, `dlq.internal` | rejected records with the reason |

Headless runs (`raildss_orchestrator`, tests and CI) pass the same envelopes in memory. With `--kafka`
the twin streams `gt.*` and `obs.*` to the broker (`docker compose up -d db kafka`).

## Versions and governance

- **Network:** `network.version` in the register. The content-only hash means a content change needs a
  new label (DR-0006). Static tables carry no policy or rule-catalogue stamp (DR-0007).
- **Parameters:** `config/parameters.yaml`, linted (`python -m raildss_params lint`). No rule literals in
  code (`tools/lint_literals.py`).
- **Rules:** `config/rule_catalogue.yaml`, linted (`python -m raildss_verifier lint`).
- **Decisions:** `docs/decisions/` (DR-0001 to DR-0008).
