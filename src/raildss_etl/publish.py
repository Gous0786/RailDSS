"""Network publication ``KTV-PSA-2025-08-r2`` (spec FR-TWIN-02, DQ-10, FR-DATA-03/04, §6.6 step 7; issue #18).

``assemble`` takes the output of the whole ETL (topology, station lines and speeds, signals and
sub-blocks, gradients and curves, WTT, freight demand), stamps it with the version in force,
validates it, and builds the files of the publication:

* one ``<table>.json`` per canonical entity, records sorted by primary key, one record per line;
* ``freight_demand_stats.json``: fitted NHPP rates and per-block time statistics;
* ``network.json``: versions, record counts per layer, and the **network hash**, a SHA-256 over the
  entity files in schema order (same inputs, same bytes, same hash);
* ``PROVENANCE.md``: the provenance card (source hashes, ETL, mapping and parameter versions,
  synthetic components and seeds) and ``MANIFEST.sha256``.

Publication is blocked while the validator reports an error (DQ-10). The files can be loaded into
PostgreSQL + PostGIS with ``load_database``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

import raildss_etl
from raildss_etl import dq, gradients, signalling, validator
from raildss_etl.mapping import REPO
from raildss_etl.rolling_stock import RollingStock
from raildss_etl.timetable import Timetable, ist
from raildss_params import Register
from raildss_schema import migrate, store
from raildss_schema.base import Stamped
from raildss_schema.entities import ENTITIES, Consist, NetworkVersion, TrainRun

PUBLISHED_DIR = REPO / "data" / "published"
STATS_FILE = "freight_demand_stats.json"
NETWORK_FILE = "network.json"
CARD_FILE = "PROVENANCE.md"
MANIFEST_FILE = "MANIFEST.sha256"
FORMAT_VERSION = "1"

LAYERS: dict[str, tuple[str, ...]] = {
    "meta": ("network_version",),
    "macro": ("operational_point", "service", "timetable_point", "train_run", "day_instance", "freight_load"),
    "meso": (
        "block_section",
        "block_section_line",
        "station_line",
        "connection_edge",
        "platform",
        "speed_profile",
    ),
    "micro": ("signal", "sub_block", "gradient", "curve"),
    "rolling_stock": ("loco_class", "vehicle_type", "consist"),
    "scenarios": ("scenario_definition",),
}
SOURCE_DIRS = (
    REPO / "data" / "raw" / "ktv-psa",
    REPO / "data" / "derived" / "ktv-psa",
    REPO / "data" / "external" / "osm-dem",
)
EXCLUDED_NAMES = {MANIFEST_FILE, CARD_FILE}
# Version governance (DR-0006, issue #63): the network hash covers the network content only. The
# ``network_version`` record is metadata about the publication (validator and DQ reports, source hashes),
# so a register edit or a regenerated report does not change the hash; a content change does, and needs a
# new ``network.version``.
METADATA_TABLES = frozenset({"network_version"})
# Twin outputs kept next to the derived inputs; they are computed from the network, not inputs to it.
NOT_SOURCES = frozenset({"ktv_psa_runtime_calibration.json", "ktv_psa_runtime_reference_seconds.json"})
ATTRIBUTION = (
    "Contains information from OpenStreetMap: © OpenStreetMap contributors, available under the Open "
    "Database License (ODbL) 1.0 (https://opendatacommons.org/licenses/odbl/1-0/); see "
    "https://www.openstreetmap.org/copyright. The gradient profiles were produced using Copernicus "
    "WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under "
    "COPERNICUS by the European Union and ESA; all rights reserved. The organisations in charge of the "
    "Copernicus programme by law or by delegation do not incur any liability for any use of the "
    "Copernicus WorldDEM-30."
)


class PublicationBlockedError(RuntimeError):
    """The validator reported errors, so the network version is not published (DQ-10)."""

    def __init__(self, report: validator.ValidationReport) -> None:
        self.report = report
        lines = [f"{r.id} {r.title}: {r.error_count} error(s)" for r in report.rules if r.error_count]
        super().__init__("publication blocked by the validator:\n  " + "\n  ".join(lines))


@dataclass(frozen=True)
class Assembled:
    network_version: str
    timetable_version: str
    tables: dict[str, list[Stamped]]
    validation: validator.ValidationReport
    dq_report: dq.DqReport
    source_hashes: dict[str, str]
    freight_stats: dict[str, Any]
    register: Register
    mapping_version: str
    synthetic: dict[str, dict[str, int]]


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_hashes() -> dict[str, str]:
    """SHA-256 of every input data file (raw dataset, derived completion data, OSM/DEM inputs)."""
    out: dict[str, str] = {}
    for root in SOURCE_DIRS:
        for p in sorted(root.rglob("*"), key=lambda q: q.relative_to(REPO).as_posix()):
            if p.is_file() and p.name not in EXCLUDED_NAMES and p.name not in NOT_SOURCES:
                out[p.relative_to(REPO).as_posix()] = sha256_bytes(p.read_bytes())
    return out


def _sorted(records: list[Any]) -> list[Any]:
    if not records:
        return records
    pk = type(records[0]).__pk__
    return sorted(records, key=lambda r: tuple(getattr(r, f) for f in pk))


def _stamp_timetable(records: list[Any], timetable_version: str) -> list[Any]:
    return [r.model_copy(update={"timetable_version": timetable_version}) for r in records]


def rolling_stock_tables(reg: Register, nv: str, tt: Timetable) -> tuple[list[Any], list[Any], list[Consist]]:
    """The #15 templates (issue #66): loco classes, vehicle types and the consist templates the twin uses,
    one passenger consist per coach count in the WTT and one freight rake per wagon family, loaded and
    empty."""
    rs = RollingStock(reg, nv)
    default = int(reg.value("runtime.calibration")["passenger_coaches_default"])
    consists: dict[str, Consist] = {}
    for n in sorted({s.coaches or default for s in tt.services}):
        c = rs.passenger(n).consist
        consists[c.id] = c
    first_code: dict[str, str] = {}
    for code, fam in sorted(rs.wagon_family.items()):
        first_code.setdefault(fam, code)
    for code in first_code.values():
        for loaded in (True, False):
            c = rs.freight(code, loaded).consist
            consists[c.id] = c
    return (
        list(rs.loco_classes.values()),
        list(rs.vehicle_types.values()),
        [consists[k] for k in sorted(consists)],
    )


def train_runs(reg: Register, nv: str, tt: Timetable, days: list[dt.date]) -> list[TrainRun]:
    """The dated WTT expansion (issue #66): every passenger run with a section row on one of ``days``,
    with the consist template of its coach count."""
    rs = RollingStock(reg, nv)
    default = int(reg.value("runtime.calibration")["passenger_coaches_default"])
    runs: dict[str, TrainRun] = {}
    for day in days:
        for run in tt.runs_on(day, ist(), nv):
            coaches = tt.service(str(run.train_id)).coaches or default
            runs.setdefault(
                run.run_id, run.model_copy(update={"consist_id": rs.passenger(coaches).consist.id})
            )
    return list(runs.values())


def _synthetic_counts(tables: dict[str, list[Stamped]]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name, rows in tables.items():
        if rows and hasattr(rows[0], "source"):
            out[name] = dict(sorted(Counter(str(getattr(r, "source")) for r in rows).items()))  # noqa: B009
    estimated = sum(1 for r in tables["station_line"] if getattr(r, "csr_status") == "estimated")  # noqa: B009
    out["station_line (csr_status)"] = {
        "estimated": estimated,
        "data": len(tables["station_line"]) - estimated,
    }
    return out


def assemble(stages: dq.Stages | None = None) -> Assembled:
    """Build, stamp and validate the network. Raises :class:`PublicationBlockedError` on errors."""
    st = stages or dq.run_stages()
    reg = st.register
    nv = str(reg.value("network.version"))
    tv = str(reg.value("timetable.version"))
    sig = signalling.build_signalling(st.topo, st.speed_result.station_lines, reg)
    # The derived gradient and curve files do not depend on the label: stamp them with this version.
    grads = [g.model_copy(update={"network_version": nv}) for g in gradients.read_gradients()]
    curves = [c.model_copy(update={"network_version": nv}) for c in gradients.read_curves()]
    report = dq.build_report(stages=st)

    locos, vehicles, consists = rolling_stock_tables(reg, nv, st.tt)
    days = sorted({d.day for d in st.demand.days})
    tables: dict[str, list[Any]] = {
        "operational_point": st.topo.operational_points,
        "block_section": st.topo.block_sections,
        "block_section_line": st.topo.block_section_lines,
        "station_line": st.speed_result.station_lines,
        "connection_edge": st.topo.connection_edges,
        "platform": st.platforms,
        "speed_profile": st.speed_result.speed_profiles,
        "gradient": grads,
        "curve": curves,
        "signal": sig.signals,
        "sub_block": sig.sub_blocks,
        "service": _stamp_timetable(st.tt.services, tv),
        "timetable_point": _stamp_timetable(st.tt.points, tv),
        "freight_load": _stamp_timetable(st.demand.loads, tv),
        "day_instance": _stamp_timetable(st.demand.days, tv),
        "train_run": _stamp_timetable(train_runs(reg, nv, st.tt, days), tv),
        "loco_class": locos,
        "vehicle_type": vehicles,
        "consist": consists,
    }
    for model in ENTITIES:
        tables.setdefault(model.__table__, [])
    tables = {name: _sorted(rows) for name, rows in tables.items() if name != "network_version"}

    completion = signalling.validate(sig, st.topo, reg)
    dq_failures = [f"{c.id} {c.title}: {c.detail}" for c in report.checks if c.status != "PASS"]
    dq_failures += [f"stage {k} failed" for k, ok in report.stage_status.items() if not ok]
    validation = validator.validate(tables, reg, st.topo.boundary_blocks, completion, dq_failures)
    if not validation.ok:
        raise PublicationBlockedError(validation)

    hashes = source_hashes()
    created = reg.get("network.version").valid_from
    record = NetworkVersion(
        network_version=nv,
        created_at=dt.datetime.combine(created, dt.time(0), tzinfo=dt.UTC),
        description=(
            "KTV-PSA (Kottavalasa Jn - Palasa, ECoR Waltair division, "
            f"{reg.value('section.length_km')['total']} km): COA master tables, WTT-style passenger "
            "schedule and the Aug 2025 freight log, completed with synthetic sub-blocks, signals, "
            "gradients and curves"
        ),
        source_hashes=hashes,
        validator_report=validation.model_dump(mode="json"),
        dq_report=report.model_dump(mode="json"),
    )
    tables = {"network_version": [record], **tables}
    stats = {
        "nhpp_rate_per_h": st.demand.nhpp_rate_per_h,
        "block_times": [b.model_dump(mode="json") for b in st.demand.block_times],
    }
    return Assembled(
        network_version=nv,
        timetable_version=tv,
        tables=tables,
        validation=validation,
        dq_report=report,
        source_hashes=hashes,
        freight_stats=stats,
        register=reg,
        mapping_version=st.spec.mapping_version,
        synthetic=_synthetic_counts(tables),
    )


# ---------------------------------------------------------------- serialisation


def dumps(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def table_file(records: list[Stamped]) -> str:
    """Records as a JSON array, one per line, keys sorted (deterministic bytes)."""
    if not records:
        return "[]\n"
    return "[\n" + ",\n".join(dumps(r.model_dump(mode="json")) for r in records) + "\n]\n"


def network_hash(files: dict[str, str]) -> str:
    """SHA-256 over the network content files in schema order, each prefixed by its table name. The
    ``network_version`` metadata record is not part of it (DR-0006)."""
    h = hashlib.sha256()
    for model in ENTITIES:
        if model.__table__ in METADATA_TABLES:
            continue
        h.update(model.__table__.encode("utf-8") + b"\0")
        h.update(files[f"{model.__table__}.json"].encode("utf-8"))
    return h.hexdigest()


def counts(tables: dict[str, list[Stamped]]) -> dict[str, int]:
    return {m.__table__: len(tables.get(m.__table__, [])) for m in ENTITIES}


def layer_of(table: str) -> str:
    return next(layer for layer, names in LAYERS.items() if table in names)


def render_card(a: Assembled, digest: str) -> str:
    reg = a.register
    sources = a.source_hashes
    status = Counter(p.status for p in reg.parameters.values())
    n = counts(a.tables)
    lines = [
        f"# Provenance card: network `{a.network_version}`",
        "",
        "Generated by `raildss network publish` (spec FR-DATA-03, FR-DATA-04). Deterministic: no timestamps.",
        "",
        "| Field (spec FR-DATA-03) | Value |",
        "|---|---|",
        "| Dataset | Network `KTV-PSA-2025-08-r2`: micro, meso and macro layers, WTT services and points, "
        "freight loads and day instances (JSON, one file per entity) |",
        "| Source | The user-supplied KTV-PSA dataset (14 files under `data/raw/ktv-psa`, see its card), the "
        "derived completion data under `data/derived/ktv-psa`, and OpenStreetMap and Copernicus DEM "
        "inputs listed in `data/external/osm-dem/inputs.yaml` |",
        "| Real / synthetic | **Mixed.** Infrastructure, timetable and freight demand are real "
        "(`source = data`). Sub-blocks, signals, gradients and curves are synthetic; PSA station-line "
        "CSR values are estimates (see the table below) |",
        f"| Network hash | `{digest}` (SHA-256 over the network content files in schema order; the "
        "`network_version` metadata record is not hashed, and a content change needs a new version: "
        "DR-0006) |",
        f"| `network_version` | `{a.network_version}` |",
        f"| `timetable_version` | `{a.timetable_version}` (on services, timetable points, freight loads, "
        "day instances) |",
        "| `policy_version`, `rule_catalogue_version` | Not stamped: the static network has no policy "
        "or rule catalogue; runtime events, plans and KPIs carry them |",
        f"| ETL version | `raildss_etl {raildss_etl.__version__}` |",
        f"| Mapping version | `{a.mapping_version}` |",
        f"| Parameter register | `{reg.version}` (spec `{reg.spec_version}`); "
        + ", ".join(f"{k} {v}" for k, v in sorted(status.items()))
        + " parameters |",
        "| Schema | canonical schema migrations: "
        + ", ".join(f"`{v}_{nm}`" for v, nm in migrate.available())
        + " |",
        f"| Validator | {a.validation.error_count} errors "
        f"({', '.join(r.id for r in a.validation.rules)} all PASS) |",
        "| Data-quality report | DQ-14..22 PASS, embedded in the `network_version` record and committed "
        "under `docs/etl/dq/` |",
        "| Integrity | `MANIFEST.sha256`, verified in CI; `raildss network publish --check` rebuilds "
        "the network and fails on any difference |",
        "| Licence | Derived from the KTV-PSA dataset (see its card) and open data. The gradient, curve "
        "and alignment data derive from OpenStreetMap (ODbL 1.0) and Copernicus DEM |",
        f"| Attribution | {ATTRIBUTION} |",
        "",
        "## Synthetic components and seeds",
        "",
        "| Component | Entities | Source flags | Parameters | Seed |",
        "|---|---|---|---|---|",
        f"| Automatic sub-blocks and twin signals (issue #13) | `sub_block`, `signal` | "
        f"{_flags(a, 'sub_block')}; {_flags(a, 'signal')} | `abs.subblock_spacing_km.ktv_vzm`, "
        "`twin.abs_signal_aspects`, `block.adequate_distance_m.macls` | none (deterministic tiling) |",
        f"| Gradients and curves (issue #14) | `gradient`, `curve` | {_flags(a, 'gradient')}; "
        f"{_flags(a, 'curve')} | `gradient.derivation` | none (deterministic from the hashed OSM and "
        "DEM inputs) |",
        f"| PSR placement and siding speeds (issue #9) | `speed_profile` | {_flags(a, 'speed_profile')} "
        "| `block.vmax_kmh`, post spacing | none (deterministic) |",
        f"| Palasa station-line CSR (issue #8) | `station_line` | {_flags(a, 'station_line (csr_status)')} "
        "| `station_line.csr.stats` | none (section median per category) |",
        "| Freight demand sampling (issue #11) | `freight_demand_stats.json` | fitted rates | none | "
        "none stored: a seed is supplied by each scenario that samples the NHPP |",
        "",
        "## Record counts",
        "",
        "| Layer | Entity | Records |",
        "|---|---|---|",
        *[f"| {layer_of(t)} | `{t}` | {c} |" for t, c in n.items()],
        "",
        "`train_run` is the dated WTT expansion: every passenger run with a section row on a day of the "
        "freight log, with its consist template. `loco_class`, `vehicle_type` and `consist` are the "
        "rolling-stock templates of issue #15 (issue #66). Scenarios are published by M1.",
        "",
        "## Source hashes (SHA-256)",
        "",
        "| File | SHA-256 |",
        "|---|---|",
        *[f"| `{p}` | `{h}` |" for p, h in sources.items()],
        "",
    ]
    return "\n".join(lines)


def _flags(a: Assembled, key: str) -> str:
    return ", ".join(f"{k} {v}" for k, v in a.synthetic.get(key, {}).items()) or "none"


def render(a: Assembled) -> dict[str, str]:
    """Every file of the publication (name -> text)."""
    files = {f"{m.__table__}.json": table_file(a.tables.get(m.__table__, [])) for m in ENTITIES}
    files[STATS_FILE] = json.dumps(a.freight_stats, sort_keys=True, indent=1) + "\n"
    digest = network_hash(files)
    n = counts(a.tables)
    meta = {
        "format_version": FORMAT_VERSION,
        "network_version": a.network_version,
        "timetable_version": a.timetable_version,
        "network_hash": digest,
        "etl_version": raildss_etl.__version__,
        "mapping_version": a.mapping_version,
        "register_version": a.register.version,
        "validator_errors": a.validation.error_count,
        "counts": n,
        "layers": {layer: {t: n[t] for t in names} for layer, names in LAYERS.items()},
    }
    files[NETWORK_FILE] = json.dumps(meta, sort_keys=True, indent=1) + "\n"
    files[CARD_FILE] = render_card(a, digest)
    manifest = {k: sha256_bytes(v.encode("utf-8")) for k, v in files.items() if k not in EXCLUDED_NAMES}
    files[MANIFEST_FILE] = "".join(f"{h}  {k}\n" for k, h in sorted(manifest.items()))
    return files


def changed_content(files: dict[str, str], out_dir: Path) -> list[str]:
    """Content files that differ from a committed publication of the same label (DR-0006).

    Empty when nothing is published there yet or only metadata changed. Otherwise the content changed
    under the same ``network_version``, which publishing must refuse: a new label is needed."""
    if not (out_dir / NETWORK_FILE).exists():
        return []
    changed = []
    for model in ENTITIES:
        name = f"{model.__table__}.json"
        path = out_dir / name
        if model.__table__ in METADATA_TABLES:
            continue
        if not path.exists() or path.read_text(encoding="utf-8") != files[name]:
            changed.append(name)
    return changed


def default_dir(network_version: str) -> Path:
    return PUBLISHED_DIR / network_version


def write(files: dict[str, str], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in out_dir.iterdir():
        if stale.is_file() and stale.name not in files:
            stale.unlink()
    paths = []
    for name, text in files.items():
        path = out_dir / name
        path.write_text(text, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def diff(files: dict[str, str], out_dir: Path) -> list[str]:
    """Names of files that differ from, or are missing in, a committed publication."""
    problems: list[str] = []
    for name, text in sorted(files.items()):
        path = out_dir / name
        if not path.exists():
            problems.append(f"{name}: missing")
        elif path.read_bytes() != text.encode("utf-8"):
            problems.append(f"{name}: differs")
    if out_dir.exists():
        problems += [
            f"{p.name}: not produced by this build" for p in sorted(out_dir.iterdir()) if p.name not in files
        ]
    return problems


# ---------------------------------------------------------------- read back


def read_export(out_dir: Path) -> tuple[dict[str, Any], dict[str, list[Stamped]], dict[str, str]]:
    """Return (network.json, records per table, raw file text) of a published directory."""
    meta = json.loads((out_dir / NETWORK_FILE).read_text(encoding="utf-8"))
    texts: dict[str, str] = {}
    tables: dict[str, list[Stamped]] = {}
    for model in ENTITIES:
        name = f"{model.__table__}.json"
        texts[name] = (out_dir / name).read_text(encoding="utf-8")
        rows = TypeAdapter(list[model]).validate_python(json.loads(texts[name]))  # type: ignore[valid-type]
        tables[model.__table__] = list(rows)
    return meta, tables, texts


def load_database(conn: Any, tables: dict[str, list[Stamped]], network_version: str) -> dict[str, int]:
    """Replace ``network_version`` in PostgreSQL + PostGIS with ``tables``."""
    by_model = {m: tables.get(m.__table__, []) for m in ENTITIES}
    return store.replace_network(conn, network_version, by_model)
