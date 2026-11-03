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
cp .env.example .env          # set POSTGRES_PASSWORD; .env is git-ignored
docker compose up -d db kafka
```

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
| `raildss_etl` | KTV–PSA ETL from COA master tables, WTT and the freight log | §6.6, issues #5–#12, #17 |
| `raildss_twin` | Digital twin: completion, running-time calculator, simulator | §3.3, issues #13–#16, #20 |
| `raildss_optimiser` | Greedy dispatcher and CP-SAT (placeholder) | §8.4, M1/M2 |
| `raildss_verifier` | Independent verification engine (placeholder). **Must not import** the optimiser, twin or ETL; enforced by import-linter | §9, NFR-MNT-02 |

Other folders: `tools/` (licence gate, dataset manifest), `tests/`, `data/` (see below).

## Data

| Folder | Contents | Rules |
|---|---|---|
| `data/raw/ktv-psa/` | The 14 KTV–PSA CSV files exactly as supplied, plus `PROVENANCE.md` and `MANIFEST.sha256` | **Read-only input.** Never edited; all cleaning happens in the ETL (spec C-13). Stored byte-for-byte (`.gitattributes`: `data/** -text`) |
| `data/derived/ktv-psa/` | `psa_station_lines_derived.csv`: Palasa line identities and **estimated** CSR from research report 06 | Estimates; see its provenance card |
| `data/interim/` | ETL intermediate output | Git-ignored, rebuilt from `data/raw` |

Verify integrity at any time:

```bash
uv run python tools/manifest.py --check
```

If the raw extract is ever replaced deliberately, regenerate its manifest with
`tools/manifest.py --write data/raw/ktv-psa --force` in a reviewed PR.
