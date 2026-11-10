# RailDSS

AI-powered **decision support for Indian Railways section controllers**, with a section-level
digital twin. RailDSS is **advisory and non-SIL**: it recommends verified precedence, crossing and
recovery options; the controller accepts, modifies or rejects them; interlocking and Kavach remain
the safety layer.

The demo section is **Kottavalasa Jn (KTV) – Palasa (PSA)**, East Coast Railway, Waltair division:
176.83 km, 22 stations, 3-line automatic block on KTV–VZM and double-line absolute block on VZM–PSA.
It is built from a real COA/WTT/freight dataset committed under `data/raw/ktv-psa/`.

> The requirements live in the **RailDSS Software Specification v1.3** (maintained as a project
> artifact). Work is tracked in GitHub issues, milestone **M0 — KTV-PSA ETL and foundations**.

## Setup

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
uv run pytest                 # includes the dataset manifest check
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
| `raildss_common` | Event envelope and shared helpers | §6.2, issue #19 |
| `raildss_schema` | Canonical data model (micro/meso/macro); strict typing | §6.1, issue #4 |
| `raildss_params` | Parameter register: every rule value is configuration with a source | C-05, App. B, issue #3 |
| `raildss_etl` | KTV–PSA ETL from COA master tables, WTT and the freight log; network publication and the `raildss` command | §6.6, issues #5–#12, #17, #18 |
| `raildss_twin` | Digital twin: completion, running-time calculator, simulator | §3.3, issues #13–#16, #20 |
| `raildss_optimiser` | Greedy dispatcher and CP-SAT (placeholder) | §8.4, M1/M2 |
| `raildss_verifier` | Independent verification engine (placeholder). **Must not import** the optimiser, twin or ETL; enforced by import-linter | §9, NFR-MNT-02 |
| `raildss_dss` | DSS online services. M1: DS-1 ingestion and data quality. **Must not import** the twin (it sees only `obs.*` / `ops.*`); enforced by import-linter | §4, FR-ING, issue #70 |

Other folders: `tools/` (licence gate, dataset manifest), `tests/`, `data/` (see below).

## Data

| Folder | Contents | Rules |
|---|---|---|
| `data/raw/ktv-psa/` | The 14 KTV–PSA CSV files exactly as supplied, plus `PROVENANCE.md` and `MANIFEST.sha256` | **Read-only input.** Never edited; all cleaning happens in the ETL (spec C-13). Stored byte-for-byte (`.gitattributes`: `data/** -text`) |
| `data/derived/ktv-psa/` | `psa_station_lines_derived.csv`: Palasa line identities and **estimated** CSR from research report 06 | Estimates; see its provenance card |
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
version stamps) and writes `data/published/KTV-PSA-2025-08/`: one JSON file per canonical entity,
`network.json` (versions, counts per layer, **network hash**), a provenance card and a manifest. It
refuses to publish while the validator reports an error. Re-run it (and `dq --update-baseline`)
after changing inputs or `config/parameters.yaml`; CI fails on any difference.

```bash
uv run raildss network publish --check              # CI gate: rebuild and compare byte for byte
uv run raildss network show KTV-PSA-2025-08         # version stamps and record count per entity
uv run raildss network load KTV-PSA-2025-08 --db-url postgresql://...   # PostgreSQL + PostGIS
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
Perturbations, the observation model, the injector and station-master agents are M1.

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

It is keyboard-first: press `?` for the shortcuts. `P` opens the recommendation, `1`/`2`/`3` selects an
option, `V` toggles preview off / before / after, `A` accepts, `M` modifies (sandbox), `R` rejects
with a reason, and `Esc` closes. Other keys: `←`/`→` pan, `+`/`−` zoom, `N` jumps to now, `Space`
plays the replay. With the mouse, the wheel zooms, dragging pans and a click selects a train.

The recommendation in the data is a **DEMO**: it holds one freight train at its entry, replayed in
the twin, until the optimiser produces real recommendations. Accept, modify and reject only write to
the on-screen decision log. The chart is an in-house D3 + Canvas component (`d3-scale`, ISC), the
DR-0001 §3.4 fallback. `npm --prefix web run licences` is the npm licence gate.

## Licence

RailDSS code is licensed under the [Apache License 2.0](LICENSE). The KTV–PSA dataset under
`data/` is used as supplied by the project owner; see its provenance cards.
Third-party notices (OpenStreetMap, Copernicus DEM) are in [NOTICE](NOTICE); licence decisions
for third-party components are recorded in [docs/decisions/](docs/decisions/README.md).
