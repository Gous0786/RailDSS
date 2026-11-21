# RailDSS

**RailDSS** is AI-powered **decision support for Indian Railways section controllers**, with a
section-level digital twin.

- **Advisory and non-SIL.** It detects conflicts and recommends verified precedence, crossing and
  recovery options. The controller accepts, modifies or rejects them.
- **Not a safety layer.** Interlocking and Kavach remain the safety layer, and RailDSS never sets
  routes.

The demo section is **Kottavalasa Jn (KTV) – Palasa (PSA)**, East Coast Railway, Waltair division:
176.83 km and 22 stations. KTV–VZM is 3-line automatic block; VZM–PSA is double-line absolute block.
It is built from a real COA, WTT and freight dataset committed under `data/raw/ktv-psa/`. The
published network is **`KTV-PSA-2025-08-r2`**.

**Documentation:**
- [docs/README.md](docs/README.md): index of decision records, KPI reports, calibration, scenarios and reviews.
- [docs/architecture.md](docs/architecture.md): pipeline, package map, import boundaries and data flow.

The requirements live in the **RailDSS Software Specification** (v1.8, a project artefact).

## Status

- **M0 (KTV-PSA ETL and foundations): done.** See DR-0002.
- **M1 (baseline loop): done, with recorded deviations.** See [DR-0008](docs/decisions/0008-m1-exit-review.md).
  - The twin runs real days.
  - Scenario library v0 has 10 scenarios.
  - The closed-loop replay runs with greedy (and optional CP-SAT) recommendations through gates G0-G3,
    the HITL lifecycle and the executor.
  - The mutation catch is 100 %, and TLA+ finds 0 violations.
  - The UI has the control chart, mimic, queue, alerts and cards.
  - Audit and replay work.
- **Known limits** (DR-0008):
  - Passenger timing: 31.9 % of spans are within 1 min of their own booked time. The ceiling of any
    per-leg model is about 52 % (DR-0005).
  - BL-0 freight has no detention model, so it is far faster than the log.
  - The planner does not yet model TSRs, point failures or late-train disruptions.
  - The DSS benefit is shown on one 2-hour demo window only.
  - Face-validity findings FV-01 to FV-06 are open ([review](docs/review/m1-face-validity.md)).

## Quick start

Needs [uv](https://docs.astral.sh/uv/) and, for the web UI, Node.js 20+.

```bash
uv python install 3.12 && uv sync      # Python 3.12 and every dependency
npm --prefix web ci                    # web UI dependencies
```

## One-command runs

| What | Command |
|---|---|
| Twin: one real day under BL-0 | `uv run python -m raildss_twin run --date 2025-08-20` |
| Closed-loop replay demo (DSS against BL-0, 08:00-10:00) | `uv run python -m raildss_orchestrator replay --report docs/kpi/replay-ktv-psa-2025-08-20.md` |
| Disruption scenarios: twin hashes | `uv run python -m raildss_twin scenario run` |
| Disruption scenarios: KPIs under BL-0 and the loop | `uv run python -m raildss_orchestrator scenarios --report docs/kpi/scenarios-v0.md` |
| Web UI (control chart; `G` mimic; queue, alerts, cards; `L` audit) | `npm --prefix web start` (opens http://localhost:5173) |
| Export a day for the web UI | `uv run python -m raildss_twin chart --date 2025-08-20` |
| Running-time calibration (DSC-10) | `uv run python -m raildss_twin calibrate` (`--write` updates the report) |
| Data checks (DQ, publication, KPI reference, manifests) | `uv run python -m raildss_etl dq --check && uv run raildss network publish --check && uv run python -m raildss_etl kpi-reference --check && uv run python tools/manifest.py --check` |

## Setup (development)

Requires [uv](https://docs.astral.sh/uv/) and, for local infrastructure, Docker.

```bash
uv python install 3.12
uv sync                       # creates .venv with dev tools
uv run pre-commit install     # optional: run checks on commit
```

## Checks (same as CI)

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run lint-imports           # import boundaries (verifier independence, NFR-MNT-02)
uv run pytest -n auto -m "not slow"   # the fast suite, in parallel (includes the dataset manifest check)
uv run pytest -n auto                 # everything, in parallel (the slow whole-day twin, loop and scenario tests too)
```

Licence gate (NFR-LIC-02), run in CI on runtime dependencies only:

```bash
uv pip install pip-licenses
uv run --no-sync pip-licenses --format=json --with-system > licences.json
uv run --no-sync python tools/check_licences.py licences.json
```

It fails on BSL, ELv2, SSPL, Prosperity, GPL or AGPL. LGPL and unknown licences are reported for review.

## Local infrastructure

```bash
cp .env.example .env          # set POSTGRES_PASSWORD and the KAFKA_*_PASSWORD values; .env is git-ignored
docker compose up -d db kafka
uv run python -m raildss_common kafka apply   # topics + ACLs from config/kafka/topics.yaml (idempotent; as admin)
uv run python -m raildss_common kafka check   # DQ-12: DSS principals cannot reach gt.*
```

Kafka clients authenticate with SASL/PLAIN as one of `admin`, `twin`, `evaluation` or `dss`
(`RAILDSS_KAFKA_USERNAME` / `RAILDSS_KAFKA_PASSWORD`). Every message uses the versioned event
envelope (`schemas/events/envelope-1.0.schema.json`, spec §6.2); see `raildss_common.envelope`
and `raildss_common.bus`. Kafka integration tests: `RAILDSS_TEST_KAFKA_BOOTSTRAP=localhost:9092 uv run pytest -m kafka`.

| Service | Image | Port |
|---|---|---|
| `db` | `timescale/timescaledb-ha:pg17.11-ts2.31.0` (PostgreSQL 17 + TimescaleDB + PostGIS) | 5432 |
| `kafka` | `apache/kafka:4.4.0` (KRaft, single node) | 9092 |

## Package layout

| Package | Purpose | Spec |
|---|---|---|
| `raildss_common` | Event envelope, Kafka bus, topic catalogue and ACL lint | §6.2, issue #19 |
| `raildss_schema` | Canonical data model (micro, meso and macro); strict typing; DDL and store | §6.1, issue #4 |
| `raildss_params` | Parameter register: every rule value is configuration with a source | C-05, App. B, issue #3 |
| `raildss_etl` | KTV-PSA ETL, DQ, validator, network publication (`raildss` command), rolling-stock templates, KPI reference | §6.6, issues #5-#18, #63, #66 |
| `raildss_twin` | Digital twin: calibration, simulator, observation model, injector, station masters, G3 sandbox, scenarios, chart and mimic export | §3.3, issues #13-#16, #20, #51-#53, #59, #87 |
| `raildss_dss` | DSS services DS-1 to DS-5 (ingestion, state, prediction, conflict detection) and the KPI engine. **Must not import** the twin | §4, FR-TWIN-14 |
| `raildss_optimiser` | Greedy rolling dispatcher and warm-started CP-SAT | §8.4, issues #75, #76 |
| `raildss_verifier` | Gates G0-G2, rule catalogue, offline auditor. **Must not import** the optimiser, twin or ETL | §9, NFR-MNT-02 |
| `raildss_hitl` | Recommendation lifecycle (TLA+-checked) and executor | §3.8, issues #82, #83 |
| `raildss_orchestrator` | Rolling decision loop, closed-loop replay, scenario report, audit replay | DS-9, issues #84, #90, #57 |
| `raildss_audit` | Hash-chained audit log | FR-AUD-01, issue #85 |

The import boundaries are enforced by import-linter; see [docs/architecture.md](docs/architecture.md).

Other folders: `tools/` (licence gate, dataset manifest), `tests/`, `data/` (see below).

## Data

| Folder | Contents | Rules |
|---|---|---|
| `data/raw/ktv-psa/` | The 14 KTV–PSA CSV files exactly as supplied, plus `PROVENANCE.md` and `MANIFEST.sha256` | **Read-only input.** Never edited; all cleaning happens in the ETL (spec C-13). Stored byte-for-byte (`.gitattributes`: `data/** -text`) |
| `data/derived/ktv-psa/` | `psa_station_lines_derived.csv` (Palasa line identities and **estimated** CSR from research report 06), the derived alignment, gradients and curves, and the running-time calibration outputs | Estimates and derived data; see the provenance card |
| `data/published/` | Network publications: `KTV-PSA-2025-08-r2` (current, with `train_run` and the rolling-stock tables) and `KTV-PSA-2025-08` (previous, kept unchanged) | A content change needs a new label (DR-0006) |
| `data/external/osm-dem/` | Provenance card for the OpenStreetMap and Copernicus DEM gap-fill inputs (issue #14) | Attribution text must be copied wherever these data are distributed or shown |
| `data/interim/` | ETL intermediate output | Git-ignored, rebuilt from `data/raw` |

Verify integrity at any time:

```bash
uv run python tools/manifest.py --check
```

If the raw extract is ever replaced deliberately, regenerate its manifest with
`tools/manifest.py --write data/raw/ktv-psa --force` in a reviewed PR.

## Parameter register (issue #3)

Every rule value and threshold lives in `config/parameters.yaml`, with a source and a status
(V / DATA / EST / U / SA / DUMMY). Code reads values only through `raildss_params`:

```python
from raildss_params import get, value

value("abs.subblock_spacing_km.ktv_vzm")  # 1.0 (DUMMY, see docs/params/dummy-values.md)
get("station_line.csr.psa").bounds  # low / high for an estimated value
```

```bash
uv run python -m raildss_params lint                    # register lint
uv run python -m raildss_params dummy-report --write    # regenerate docs/params/dummy-values.md
uv run python tools/lint_literals.py                    # no literal rule values in src/
```

Structural constants that are not rule values (e.g. an EPSG code) go in
`config/literal_allowlist.txt` with a reason, and are reviewed in the PR.

## Canonical schema (issue #4)

`raildss_schema.entities` defines the M0 entities as Pydantic models. Every entity carries the
version stamps and a `raw` field for unmapped source columns. Datetimes are stored in UTC.
The SQL migrations (`migrations/`) and JSON Schemas (`schemas/`) are **generated** from the models:

```bash
uv run python -m raildss_schema generate      # after changing a model
uv run python -m raildss_schema check         # CI: fails if generated files are stale
uv run python -m raildss_schema migrate up --url postgresql://raildss:<pw>@localhost:5432/raildss
RAILDSS_TEST_DB_URL=postgresql://... uv run pytest -m db   # DB round-trip tests
```

## KTV-PSA ETL (issues #5, #6)

`config/mapping/ktv_psa.yaml` maps every column of the 14 source files to a canonical field, to `raw`
(kept, unused) or to `ignore` (dropped, with a reason). The ETL reads file paths, cleaning policy, date
formats and expected row counts from it. A Markdown view for review: `docs/etl/mapping-ktv-psa.md`.

```bash
uv run python -m raildss_etl mapping-check         # mapping vs dataset headers and schema
uv run python -m raildss_etl mapping-doc --write   # regenerate the Markdown view
uv run python -m raildss_etl stage1                # load & clean -> data/interim/ktv-psa/*.parquet
uv run python -m raildss_etl geo-fetch              # cache + verify the OSM and GLO-30 inputs (issue #14)
uv run python -m raildss_etl gradients             # synthetic gradients and curves -> data/derived/ktv-psa
uv run python -m raildss_etl dq --check              # whole ETL in memory -> DQ report; fails on drift from docs/etl/dq baseline
uv run python -m raildss_etl kpi-reference --check  # historical freight KPIs (FR-KPI-11, BL-5) vs docs/kpi; --write to refresh
```

Stage 1 drops spreadsheet-export columns, blank and repeated-header rows and exact duplicates, parses
the three date formats (year 99 -> 2099), localises freight timestamps to IST, splits WTT seconds into
day offset + time of day, and writes `stage1_report.json`. It never writes inside `data/raw/`.

## Network publication (issue #18)

`raildss network publish` runs the whole ETL, validates the network (DQ-10: crossing stations have
loops with CSR > 0, block sections have a system and a length, routes are consistent, references and
version stamps) and writes `data/published/KTV-PSA-2025-08-r2/`: one JSON file per canonical entity,
`network.json` (versions, counts per layer, **network hash**), a provenance card and a manifest. It
refuses to publish while the validator reports an error. Re-run it (and `dq --update-baseline`)
after changing inputs or `config/parameters.yaml`; CI fails on any difference. The network hash covers
the network content only, so a register edit keeps it; a content change under an existing
`network_version` is refused and needs a new `network.version` (DR-0006).

```bash
uv run raildss network publish --check              # CI gate: rebuild and compare byte for byte
uv run raildss network show KTV-PSA-2025-08-r2         # version stamps and record count per entity
uv run raildss network load KTV-PSA-2025-08-r2 --db-url postgresql://...   # PostgreSQL + PostGIS
```

## Twin skeleton: one real day (issue #20)

`python -m raildss_twin run` simulates one day on the published network under policy BL-0 (booked order,
first come first served, nearest free permitted line). A SimPy meso model: COA block lines on VZM-PSA
and synthetic sub-blocks on KTV-VZM are unit resources, station lines too, reception and despatch only
along Connections edges, running times from the calibration of issue #16, dwell from the WTT. The input
is the WTT for a date plus the freight `DayInstance` of that day. It emits `gt.*` events (evaluation
only) and reports deadlocks with their location instead of hiding them.

```bash
uv run python -m raildss_twin run --date 2025-08-20                  # headless, about 20 s
uv run python -m raildss_twin run --date 2025-08-20 --seed 7 --trace-out trace.jsonl
uv run python -m raildss_twin run --speed 60 --until-h 1 --kafka     # x60 clock, stream gt.* to Kafka
uv run python -m raildss_twin run --speed 30 --control               # then type: pause | resume | speed 60
```

The trace hash is a SHA-256 over the emitted events in order; the same plans and seed give the same hash.
M1 added the following:
- seeded perturbations (`--perturb`);
- the observation model (`obs.*` under profiles D0-D3, `--profile`);
- the disruption injector;
- the station-master agents.

Passenger runs come from the published `train_run`, and the rolling stock from the published templates.

`python -m raildss_twin displib --from-h 0 --to-h 2` converts a window of the day into the solver-facing,
DISPLIB-style IR (operations, resources, release times, delay objective); see `docs/twin/displib-ir.md`.
No DISPLIB instance or verifier is used or committed (licence unstated).

## Ingestion and data quality (issue #70)

`python -m raildss_dss ingest` is DS-1. It validates every `obs.*` / `ops.*` record (invalid ones go to
the dead-letter topic), drops duplicate `event_id`s and orders events on event time with a watermark
per source. Events more than `ingest.late_threshold_min` behind are kept but flagged `late`. It raises
a P4 "data stale" alert when a source misses `ingest.heartbeat_missed_limit` heartbeats, flags trains
with no fix for `state.stale_threshold_min`, and prints per-source metrics.

```bash
uv run python -m raildss_twin run --until-h 2 --profile D2 --trace-out trace.jsonl
uv run python -m raildss_dss ingest --trace trace.jsonl              # replay a twin trace
uv run python -m raildss_dss ingest --kafka --speed 60               # live, as the dss principal
```

`python -m raildss_dss state --trace trace.jsonl --every-min 10` replays the same trace through DS-3 state
estimation (issue #71). Each train gets its resource (station or block), km, speed, delay and fix age,
and is flagged stale (position unknown, never extrapolated) after `state.stale_threshold_min`. It prints
the snapshot `S_t` versions and hashes; `--out snap.json` writes the last snapshot.

`python -m raildss_dss predict-eval --write` trains the DS-4 process-time tables (freight P10/P50/P90
per block, direction and loaded/empty) on the first `pred.train_days` days of the freight log. It then
scores ETA accuracy on the held-out days against persistence and schedule baselines, at
`pred.eval_horizons_min`, and writes `docs/dss/eta-evaluation.md` (issue #72).
`raildss_dss.predict.Predictor` propagates ETA/ETD P50 and P90 from a state snapshot under a plan's
orderings.

`python -m raildss_dss conflicts --trace trace.jsonl --at-h 2` runs DS-5 conflict detection (issue #73)
at a time of a replayed trace, using state, prediction and resource occupations over `cd.horizon_min`.
It reports types 1-4, 6 and 7 with priority, lead time, the P90 near-conflict band and
`NO_FEASIBLE_RESOLUTION`. `raildss_dss.conflicts.StabilityTracker` reports alert stability.

## Web UI: control chart (issue #86)

A React app in `web/` shows the COA-style time-distance control chart of a twin day on KTV-PSA:
stations and loops on the distance axis, the twin's actual train lines (solid), the WTT (faint), the
"now" line, and a recommendation preview with a before/after toggle. Run it with one command (needs
Node.js 20+):

```bash
npm --prefix web start          # installs, starts Vite and opens http://localhost:5173
```

The chart reads `web/public/data/<day>.json`, which comes from a twin replay. The data for 2025-08-20
is committed. Export another day with:

```bash
uv run python -m raildss_twin chart --date 2025-08-12
```

`G` (or the Mimic button) switches to the **schematic mimic** (issue #87, FR-UI-06). It is a
straight-line SVG generated from the published layout: main lines, loops with their usable length,
block sections split into automatic sub-blocks, and signals. It shows the twin run's occupancy and
train icons (number, direction and delay colour) at the time cursor. It is supervisory only, with no
route setting.

It is keyboard-first: press `?` for the shortcuts. `P` opens the recommendation, `1`/`2`/`3` selects an
option, `V` toggles preview off / before / after, `A` accepts, `M` modifies (sandbox), `R` rejects
with a reason, and `Esc` closes. Other keys: `←`/`→` pan, `+`/`−` zoom, `N` jumps to now, `Space`
plays the replay. With the mouse, the wheel zooms, dragging pans and a click selects a train.

The recommendation in the data is a **DEMO**: it holds one freight train at its entry, replayed in
the twin, until the optimiser produces real recommendations. Accept, modify and reject only write to
the on-screen decision log. The chart is an in-house D3 + Canvas component (`d3-scale`, ISC), the
DR-0001 §3.4 fallback. `npm --prefix web run licences` is the npm licence gate and
`npm --prefix web run licences:report` also writes `web/licences.json` (CI uploads it as an artifact).

Packaging and attributions (issue #89): the chart and the About page are separate chunks
(`npm --prefix web run check-chunks` after a build). The **About** button (or `I`) shows the OSM and
Copernicus attributions, the production dependencies with their licences and the Grafana note; the chart
carries a corner "© OpenStreetMap contributors" link (DR-0001 §5.3). Production dependencies are pinned
exactly. To show a "Health ↗" link to Grafana (an unmodified, separate service, DR-0001 §4), start the UI with
`VITE_GRAFANA_URL=http://localhost:3000 npm --prefix web start`.

## Greedy priority dispatcher (issue #75)

`python -m raildss_optimiser greedy --at-h 8` plans the next `cd.horizon_min` of a twin day from 08:00. It
serves every queue by `policy.weights` (relief trains first, ties to the earliest arrival), chooses
lines (own direction before MID), and checks the plan with gate G2. `--close DUSI~PDU/UP@8.5-9.5`
closes a line for single-line working and `--exit-closed PSA@8.5-9` closes a boundary exit (trains
are held at the last block station). `--conflicts` runs the DSS chain (DS-1 to DS-5) up to that time
and prints a recommendation for every detected conflict. `RollingDispatcher` re-plans on
`opt.replan_triggers` and at least every `opt.replan_max_interval_min`, keeping decisions inside
`opt.freeze_window_min`.

## Closed-loop replay of a real day (issues #84, #90)

`python -m raildss_orchestrator replay --report docs/kpi/replay-ktv-psa-2025-08-20.md` is the M1 demo:
the twin plays 20 Aug 2025 (WTT plus the real freight day) from 08:00 to 10:00, the DSS chain
(DS-1 to DS-5) reads its observed feeds, and every re-plan (on triggers and at least every 5 min, OD-25)
builds greedy options, checks them with gates G0-G3 and releases only verified ones into the
recommendation lifecycle. The evaluation policy accepts the best option, the executor issues its control
orders and the station masters acknowledge them; the day is then re-simulated under those orders. The
report compares the KPIs of the DSS arm with BL-0 (same seed, same demand) and lists what the loop did.
`--from-h/--to-h` set the window, `--cpsat` adds warm-started CP-SAT options, `--planner cpsat` makes CP-SAT the primary planner with freeze and stability (issue #152), `--close SEG/LINE@FROM-TO`
adds a closure and `--out run.json` keeps the full log. `python -m raildss_orchestrator run` is the same
loop without the report. See `docs/dss/orchestrator.md`. The result of the committed run is in
`docs/kpi/replay-ktv-psa-2025-08-20.md`.

The committed run (greedy options, 08:00-10:00, regenerated for DR-0008) executes 37 accepted
recommendations in the twin and ends ahead of BL-0: A5 weighted delay -6.1 % (13211 against 14071
train-min), P90 exit delay -19.1 min, unplanned stops -39. All 81 candidate plans pass gates G0-G3. The
greedy plan is a pure function of the day and its directives, so an unchanged world reproduces the
decisions of earlier plans. Frozen decisions (commit window, started, accepted orders) are therefore only
checked (VER-FRZ-01). They are imposed as holds only when the world changes (a closure or a closed exit),
because a hold sends a train that was queueing to the back of its queue (issue #127). It is still one 2-hour demo window, not a benefit claim.

## Audit and replay (issue #85)

`python -m raildss_orchestrator run --from-h 8 --to-h 9 --audit-dir audit` records the loop in an
append-only, hash-chained audit log (`raildss_audit`, one chain per section and day, the head anchored
daily): every alert and conflict, every planning cycle (`plan_run`: context, state hash and version, register,
network and rule-catalogue versions, seed, solver settings, all candidates with their G0-G3 results), every
option shown for a decision with its explanation text, every decision (accept, reject with a reason code, or
the declared evaluation policy, with the time to decision and the KPI delta), every control order and
acknowledgement, and every outcome. Controllers appear only as pseudonyms; `AuditLog.config_change` records
policy, rule-catalogue, threshold and user changes with before, after and approvers (FR-AUD-07).

- `python -m raildss_audit verify --dir audit` checks the chain (a changed, deleted, inserted or reordered
  record, or a truncated day, fails); `query` filters by kind, train, time window, controller, incident and
  recommendation; `export --out web/public/data/audit.json` feeds the web UI.
- `python -m raildss_orchestrator audit-replay --audit-dir audit --rec REC-0001` replays a recommendation
  from its record and compares the canonical JSON byte for byte: the greedy plan is recomputed, a CP-SAT plan
  is re-verified by the G3 sandbox replay (it is not solved again, G1 is carried from the record), and the
  recommended orders are derived again.
- Web UI: `L` (or the Audit button) opens the audit and replay view: filterable log, a timeline scrubber that
  moves the chart to that time under the REPLAY banner, and each recommendation card as it was shown. The
  committed `web/public/data/audit.json` is a short demo run.

## Scenario library v0 (issues #21, #57)

`python -m raildss_twin scenario run [SCN-05 ...]` plays the scenarios of `scenarios/v0` in the twin and
checks each `gt.*` trace hash. All ten are executable: the disruption ones (SCN-02 late premium train,
SCN-03 speed restriction, SCN-05 and SCN-20 single-line working, SCN-06 loop out of use) run with the
disruption injector, which applies the scripted incident to the ground truth and announces it on
`ops.disruptions`; CI runs all ten. `python -m raildss_orchestrator scenarios --report
docs/kpi/scenarios-v0.md` reports each scenario's KPIs under BL-0 and, for the two line-block scenarios, the
decision loop against BL-0 under the same closure.

**Library v1** (issue #165): `python -m raildss_twin scenario check|index|run` now covers the v0 ten plus
the twelve definitions in `scenarios/v1` (SCN-04, 07-11, 14, 15, 19, 21, 23, 24): 22 scenarios, indexed by
family in [`scenarios/v1/INDEX.md`](scenarios/v1/INDEX.md). SCN-07, 14, 19 and 21 run in the twin today and
carry their `gt.*` hash; the other eight name the component they wait for under `requires` (loco-failure,
OHE and crew, special-train and closed-exit injectors, #146-#148, and the SCN-24 sensitivity run) and get
their hash with `scenario run --record` when it lands. `--library v0` keeps the v0 view.

## Licence

RailDSS code is licensed under the [Apache License 2.0](LICENSE). The KTV–PSA dataset under
`data/` is used as supplied by the project owner; see its provenance cards.
Third-party notices (OpenStreetMap, Copernicus DEM) are in [NOTICE](NOTICE); licence decisions
for third-party components are recorded in [docs/decisions/](docs/decisions/README.md).
