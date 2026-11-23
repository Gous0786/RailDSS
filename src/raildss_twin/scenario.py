"""Scenario format and scenario library v0 (spec FR-SCN-01, FR-SCN-04, REC-20, §12.1; issue #21).

A scenario is a versioned, seeded YAML file (``scenarios/v0/SCN-xx.yaml``) holding the network version,
the day instance (WTT date plus a real freight day, or an NHPP seed), traffic density, degradation
profile, scripted disruptions (type, location, start, duration distribution, ETR and its uncertainty)
and the expected ground-truth trace hash.

* Every scenario is **executable**: it runs in the twin (:func:`run_scenario`) and its ``gt.*`` trace hash
  must equal ``expected_gt_hash``. Scripted disruptions are applied by the injector (#53, #56):
  :meth:`ScenarioDisruption.realize` turns one into the injector's
  :class:`~raildss_twin.injector.Disruption`, drawing its duration from the scenario seed, and an
  ``entry_delay`` is a late entry of one train (:meth:`ScenarioDisruption.entry_delay_s`).
* ``requires`` stays in the format for scenarios that need a component that is not there yet (issue #57
  removed ``injector`` from the v0 library); such a scenario is validated, not executed.
* **Library v1** (issue #165, spec FR-SCN-04) is the v0 ten (``scenarios/v0``) plus the twelve scenarios of
  ``scenarios/v1``: :func:`load_library_v1`. A v1 scenario whose injector is not built yet names it in
  ``requires`` (the issue that builds it) and becomes executable when the tag is removed and its hash is
  recorded (``scenario run --record``).
"""

from __future__ import annotations

import datetime as dt
import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raildss_etl.mapping import REPO
from raildss_twin.injector import SECONDS_PER_MIN, Disruption
from raildss_twin.sim import SECONDS_PER_HOUR

SCHEMA_VERSION = 1
NETWORK_VERSION = "KTV-PSA-2025-08-r2"
LIBRARY_DIR = REPO / "scenarios" / "v0"
INDEX_FILE = LIBRARY_DIR / "INDEX.md"
V1_DIR = REPO / "scenarios" / "v1"
V1_INDEX_FILE = V1_DIR / "INDEX.md"
DISRUPTION_TYPES = (
    "line_block",
    "point_failure",
    "signal_failure",
    "tsr",
    "loco_failure",
    "entry_delay",
    "ohe_block",
    "ohe_trip",
    "crew_hours",
    "special_train",
    "exit_blocked",
)
INJECTOR_KINDS = (  # built today
    "line_block",
    "point_failure",
    "signal_failure",
    "tsr",
    "loco_failure",
    "ohe_block",
    "ohe_trip",
    "crew_hours",
)
# Components a scenario waits for: ``injector`` (the v0 tag, no scenario carries it any more) and, in M2,
# the injectors of #146 (loco and signal failure), #147 (OHE and crew hours) and #148 (special train and
# closed exit), and the parameter sensitivity run of SCN-24.
# Kinds the scenario plays itself, not the injector: a late entry (directive), an inserted special train
# (an extra plan) and a closed exit (a boundary acceptance window, a directive).
SCENARIO_KINDS = ("entry_delay", "special_train", "exit_blocked")
REQUIRES = ("injector", "injector-loco", "injector-ohe-crew", "injector-special-exit")
Requirement = Literal["injector", "injector-loco", "injector-ohe-crew", "injector-special-exit"]
# Register keys a scenario's ``sensitivity`` may set to a bound, and the station whose lines they size:
# the run applies the bound to the published station lines of that station (SCN-24).
SENSITIVITY_STATIONS = {"station_line.csr.psa": "PSA"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
FREIGHT_DAY_PREFIX = "aug25-day-"
HASH_LINE = re.compile(r"^expected_gt_hash:.*$", re.MULTILINE)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Uniform(_Strict):
    """A duration drawn uniformly between the bounds, in minutes (seeded)."""

    low_min: float = Field(gt=0)
    high_min: float = Field(gt=0)

    @model_validator(mode="after")
    def _ordered(self) -> Uniform:
        if self.high_min < self.low_min:
            raise ValueError("high_min is below low_min")
        return self


class Etr(_Strict):
    """The announced ETR: the real end plus ``offset_min``, with an uncertainty ``sigma_min``."""

    offset_min: float = 0.0
    sigma_min: float = Field(default=0.0, ge=0)


class ScenarioDisruption(_Strict):
    incident_id: str = Field(pattern=r"^[A-Za-z0-9._:-]+$")
    type: Literal[
        "line_block",
        "point_failure",
        "signal_failure",
        "tsr",
        "loco_failure",
        "entry_delay",
        "ohe_block",
        "ohe_trip",
        "crew_hours",
        "special_train",
        "exit_blocked",
    ]
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM on the scenario day")
    duration_min: Uniform | None = None  # every type but special_train (an insertion has no duration)
    etr: Etr | None = None
    block_id: str | None = None
    line_id: str | None = None  # None: every line of the block
    station: str | None = None
    line_no: int | None = None
    train_no: str | None = None
    speed_kmh: float | None = Field(default=None, gt=0)
    through_block: str | None = (
        None  # line_block, ohe_block, ohe_trip: from block_id to this block, inclusive
    )
    category: str | None = None  # special_train: the precedence class of the inserted train
    direction: Literal["DN", "UP"] | None = None  # special_train: the direction it runs
    boundary: str | None = None  # exit_blocked: a boundary of config/boundaries.yaml
    single_line_working: bool = False  # line_block, loco_failure: work the other line both ways meanwhile
    block_instrument: bool = False  # signal_failure: block instruments failed too, paper line clear (#146)
    note: str = ""

    @model_validator(mode="after")
    def _located(self) -> ScenarioDisruption:
        need = {
            "line_block": ("block_id",),
            "signal_failure": ("block_id",),
            "tsr": ("block_id", "speed_kmh"),
            "point_failure": ("station", "line_no"),
            "loco_failure": ("train_no",),
            "entry_delay": ("train_no", "station"),
            "ohe_block": ("block_id",),
            "ohe_trip": ("block_id",),
            "crew_hours": ("train_no", "station"),
            "special_train": ("train_no", "station", "category", "direction"),
            "exit_blocked": ("boundary",),
        }[self.type]
        if self.type != "special_train":
            need = (*need, "duration_min")
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.type} needs {', '.join(missing)}")
        return self

    @property
    def start_s(self) -> float:
        hours, minutes = self.start.split(":")
        return int(hours) * SECONDS_PER_HOUR + int(minutes) * SECONDS_PER_MIN

    def entry_delay_s(self, seed: int) -> float:
        """Seconds an ``entry_delay`` train enters late: drawn from the duration range with the seed."""
        assert self.duration_min is not None
        rng = random.Random(f"raildss-scn/{seed}/{self.incident_id}")
        return rng.uniform(self.duration_min.low_min, self.duration_min.high_min) * SECONDS_PER_MIN

    def realize(self, seed: int, day: dt.date) -> Disruption | None:
        """The injector disruption for this seed; ``None`` for an ``entry_delay`` (not an injector kind)."""
        if self.type in SCENARIO_KINDS:  # played by the scenario (late entry, special train, closed exit)
            return None
        if self.type not in INJECTOR_KINDS or self.duration_min is None:
            raise ValueError(f"{self.incident_id}: the {self.type} injector is not built yet")
        rng = random.Random(f"raildss-scn/{seed}/{self.incident_id}")
        duration_s = rng.uniform(self.duration_min.low_min, self.duration_min.high_min) * SECONDS_PER_MIN
        etr_s = (
            None if self.etr is None else self.start_s + duration_s + self.etr.offset_min * SECONDS_PER_MIN
        )
        run_id = f"{self.train_no}@{day.isoformat()}" if self.train_no else None
        return Disruption(
            incident_id=self.incident_id,
            kind=cast(Any, self.type),  # one of INJECTOR_KINDS, checked above
            start_s=self.start_s,
            duration_s=duration_s,
            etr_s=etr_s,
            etr_sigma_min=None if self.etr is None else self.etr.sigma_min,
            block_id=self.block_id,
            line_id=self.line_id,
            station=self.station,
            line_no=self.line_no,
            run_id=run_id,
            speed_kmh=self.speed_kmh,
            through_block=self.through_block,
            single_line_working=self.single_line_working,
            block_instrument=self.block_instrument,
        )


class Scenario(_Strict):
    schema_version: Literal[1]
    id: str = Field(pattern=r"^SCN-\d\d$")
    title: str
    family: str
    spec: str = Field(description="requirement source, e.g. 'R04 §11.1; spec §12.1'")
    network_version: str
    wtt_date: dt.date
    freight_day: str | None = Field(
        default=None, description="a real freight day instance, e.g. aug25-day-01"
    )
    nhpp_seed: int | None = None
    seed: int
    density: float = Field(default=1.0, gt=0)
    degradation_profile: Literal["D0", "D1", "D2", "D3"] = "D1"
    disruptions: list[ScenarioDisruption] = Field(default_factory=list)
    requires: list[Requirement] = Field(default_factory=list)  # components not built yet; none in v0
    base: list[str] = Field(default_factory=list, description="scenarios this one is run on top of")
    sensitivity: dict[str, Literal["low", "high"]] = Field(
        default_factory=dict, description="register key -> bound to use (SCN-24: station_line.csr.psa low)"
    )
    expected_gt_hash: str | None = None
    notes: str = ""

    @model_validator(mode="after")
    def _consistent(self) -> Scenario:
        if (self.freight_day is None) == (self.nhpp_seed is None):
            raise ValueError("give exactly one of freight_day and nhpp_seed")
        if (
            self.freight_day is not None
            and self.freight_day != f"{FREIGHT_DAY_PREFIX}{self.wtt_date.day:02d}"
        ):
            raise ValueError(f"freight_day {self.freight_day} is not the instance of {self.wtt_date}")
        if self.requires:
            if self.expected_gt_hash is not None:
                raise ValueError("a scenario that is not executable has no expected_gt_hash")
        elif self.expected_gt_hash is None or not HASH_RE.match(self.expected_gt_hash):
            raise ValueError("an executable scenario needs expected_gt_hash (64 hex digits)")
        return self

    @property
    def executable(self) -> bool:
        return not self.requires

    @property
    def day_instance(self) -> str:
        return self.freight_day or f"nhpp-{self.nhpp_seed}"


# ---------------------------------------------------------------------------------------- library
def load(path: Path) -> Scenario:
    sc = Scenario.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    if path.stem != sc.id:
        raise ValueError(f"{path.name}: the file name must be the scenario id {sc.id}")
    return sc


def load_library(directory: Path = LIBRARY_DIR) -> list[Scenario]:
    return [load(p) for p in sorted(directory.glob("SCN-*.yaml"))]


def load_library_v1() -> list[Scenario]:
    """Library v1: the v0 ten plus the scenarios of ``scenarios/v1``, by id (issue #165)."""
    return sorted([*load_library(LIBRARY_DIR), *load_library(V1_DIR)], key=lambda s: s.id)


def path_of(sc: Scenario) -> Path:
    """The file of a scenario: ``scenarios/v1`` first, then v0."""
    for directory in (V1_DIR, LIBRARY_DIR):
        if (directory / f"{sc.id}.yaml").exists():
            return directory / f"{sc.id}.yaml"
    raise FileNotFoundError(sc.id)


def check_library(scenarios: Sequence[Scenario]) -> list[str]:
    """Problems of a library as a whole: duplicate ids, unknown bases, sensitivity keys without bounds."""
    from raildss_params import load as load_register

    ids = [s.id for s in scenarios]
    problems = [f"{i}: defined twice" for i in sorted({i for i in ids if ids.count(i) > 1})]
    register = load_register()
    for s in scenarios:
        problems += [f"{s.id}: base {b} is not in the library" for b in s.base if b not in ids]
        for key in s.sensitivity:
            if key not in SENSITIVITY_STATIONS:
                problems.append(f"{s.id}: sensitivity key {key} is not one the scenarios can apply")
            try:
                has_bounds = register.get(key).bounds is not None
            except KeyError:
                has_bounds = False
            if not has_bounds:
                problems.append(f"{s.id}: sensitivity key {key} has no bounds in the register")
    return problems


def check_references(sc: Scenario, meta: Mapping[str, Any], tables: Mapping[str, Sequence[Any]]) -> list[str]:
    """Problems with the network the scenario names: version, blocks, lines, stations, loops."""
    problems = []
    if sc.network_version != meta["network_version"]:
        problems.append(
            f"network_version {sc.network_version} is not the published {meta['network_version']}"
        )
    blocks = {b.id for b in tables["block_section"]}
    lines = {(x.block_section_id, x.line_id) for x in tables["block_section_line"]}
    stations = {o.code for o in tables["operational_point"]}
    loops = {(x.op_code, x.line_no) for x in tables["station_line"]}
    for d in sc.disruptions:
        if d.block_id is not None and d.block_id not in blocks:
            problems.append(f"{d.incident_id}: block {d.block_id} is not in the network")
        if d.block_id is not None and d.line_id is not None and (d.block_id, d.line_id) not in lines:
            problems.append(f"{d.incident_id}: block {d.block_id} has no line {d.line_id}")
        if d.station is not None and d.station not in stations:
            problems.append(f"{d.incident_id}: station {d.station} is not in the network")
        if d.type == "point_failure" and (d.station, d.line_no) not in loops:
            problems.append(f"{d.incident_id}: station {d.station} has no line {d.line_no}")
        if d.through_block is not None and d.through_block not in blocks:
            problems.append(f"{d.incident_id}: block {d.through_block} is not in the network")
        if d.boundary is not None and d.boundary not in load_boundaries().boundaries:
            problems.append(f"{d.incident_id}: boundary {d.boundary} is not in config/boundaries.yaml")
    return problems


def load_boundaries() -> Any:
    from raildss_etl.boundaries import load_config

    return load_config()


def index_markdown(scenarios: Sequence[Scenario], *, v1: bool = False) -> str:
    """The library index, by family (v0 by default; ``v1`` for the v0 ten plus the v1 twelve)."""
    waiting = [s for s in scenarios if not s.executable]
    out = (
        [
            "# Scenario library v1 (KTV-PSA)",
            "",
            "Generated by `python -m raildss_twin scenario index --write` (spec FR-SCN-04, issue #165).",
            f"{len(scenarios)} scenarios: the v0 ten (`scenarios/v0`) and the v1 definitions",
            "(`scenarios/v1`).",
            f"{len(scenarios) - len(waiting)} are *executable* (they run in the twin and carry the expected",
            f"`gt.*` trace hash); {len(waiting)} wait for the component named under `requires` and become",
            "executable when it lands (M2-T3 to T5: #146, #147, #148), then get their hash with",
            "`scenario run --record`.",
        ]
        if v1
        else [
            "# Scenario library v0 (KTV-PSA)",
            "",
            "Generated by `python -m raildss_twin scenario index --write` (spec FR-SCN-04, issue #21).",
            f"{len(scenarios)} scenarios. *Executable* ones run in the twin and carry the expected `gt.*` trace",  # noqa: E501
            "hash, the disruption scenarios included (issue #57).",
        ]
    )
    for family in sorted({s.family for s in scenarios}):
        out += [
            "",
            f"## {family}",
            "",
            "| ID | Title | WTT date | Freight day | Seed | Profile | Status |",
            "|---|---|---|---|---:|---|---|",
        ]
        for s in (x for x in scenarios if x.family == family):
            status = "executable" if s.executable else "requires: " + ", ".join(s.requires)
            out.append(
                f"| {s.id} | {s.title} | {s.wtt_date} | {s.day_instance} | {s.seed} | "
                f"{s.degradation_profile} | {status} |"
            )
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------------------- running
def entry_delays(sc: Scenario) -> dict[str, float]:
    """The scripted late entries of a scenario: run id -> seconds late."""
    return {
        f"{d.train_no}@{sc.wtt_date.isoformat()}": d.entry_delay_s(sc.seed)
        for d in sc.disruptions
        if d.type == "entry_delay"
    }


def injected(sc: Scenario) -> list[Disruption]:
    """The scenario's disruptions for the injector (an entry delay is not one)."""
    return [r for d in sc.disruptions if (r := d.realize(sc.seed, sc.wtt_date)) is not None]


def exit_windows(sc: Scenario) -> dict[str, list[tuple[float, float]]]:
    """The closed exits of a scenario: station -> [(from, to) in seconds], drawn from the seed.

    A boundary acceptance window (FR-OPT-20) closes the exit of the boundary's station: a train whose
    last station in the section is that station stands at the station before it until the window ends.
    The twin works at the granularity of the station, so the other boundary of the station is closed too."""
    out: dict[str, list[tuple[float, float]]] = {}
    boundaries = load_boundaries().boundaries
    for d in sc.disruptions:
        if d.type != "exit_blocked" or d.boundary is None:
            continue
        rng = random.Random(f"raildss-scn/{sc.seed}/{d.incident_id}")
        assert d.duration_min is not None
        length_s = rng.uniform(d.duration_min.low_min, d.duration_min.high_min) * SECONDS_PER_MIN
        out.setdefault(boundaries[d.boundary].station, []).append((d.start_s, d.start_s + length_s))
    return out


def special_plans(world: Any, sc: Scenario) -> list[Any]:
    """The unplanned special trains of a scenario as plans: no booked times, only the entry time at the
    entry station (as a freight train), passenger rolling stock and the scenario's precedence class.
    They run from the entry station to the end of the section in their direction."""
    from raildss_twin.plans import Node, Plan

    stations = world.layout.block_stations()
    plans = []
    for d in sc.disruptions:
        if d.type != "special_train":
            continue
        assert d.station is not None and d.train_no is not None
        i = stations.index(d.station)
        path = stations[i:] if d.direction == "DN" else stations[: i + 1][::-1]
        plans.append(
            Plan(
                run_id=f"{d.train_no}@{sc.wtt_date.isoformat()}",
                kind="passenger",
                name=f"special train {d.train_no}",
                template=world.timing.stock.passenger(world.timing.default_coaches),
                nodes=[Node(op, d.start_s, d.start_s, False) for op in path],
                entry_s=d.start_s,
                timetabled=False,
                category=str(d.category),
            )
        )
    return plans


def run_kwargs(sc: Scenario, world: Any = None) -> dict[str, Any]:
    """What ``run.run_day`` needs to play the scenario: its seed, freight day and disruptions.

    ``world`` is needed only for a scenario with a special train: its plan is added to the day's plans."""
    from raildss_twin import run
    from raildss_twin.injector import Injector
    from raildss_twin.sim import Directives

    scripted, delays, exits = injected(sc), entry_delays(sc), exit_windows(sc)
    kw: dict[str, Any] = {
        "seed": sc.seed,
        "day_instance": sc.freight_day,
        "directives": Directives(entry_delays=delays, exit_windows=exits) if delays or exits else None,
        "setup": (lambda s: Injector(s, scripted)) if scripted else None,
    }
    special = special_plans(world, sc) if world is not None else []
    if special:
        plans, _, _ = run.plans_for(world, sc.wtt_date, sc.freight_day)
        kw["plans"] = sorted([*plans, *special], key=lambda p: (p.entry_s, p.run_id))
    return kw


def sensitivity_world(world: Any, sc: Scenario) -> Any:
    """The world with the scenario's ``sensitivity`` applied: the register bound (low or high) replaces
    the CSR of the lines of the key's station (the PSA loops and mains for ``station_line.csr.psa``).
    The published tables of ``world`` are not changed; a scenario without a sensitivity gets ``world``.
    """
    if not sc.sensitivity:
        return world
    from raildss_twin import run
    from raildss_twin.layout import build_layout
    from raildss_twin.plans import Timing

    tables = dict(world.tables)
    for key, which in sc.sensitivity.items():
        station = SENSITIVITY_STATIONS[key]
        bounds = getattr(world.register.get(key).bounds, which)  # {loop: m, main: m}
        tables["station_line"] = [
            x.model_copy(update={"csr_m": float(bounds[x.kind])})
            if x.op_code == station and x.kind in bounds
            else x
            for x in tables["station_line"]
        ]
    return run.World(tables, build_layout(tables), Timing(tables, world.register), world.register)


def run_scenario(world: Any, sc: Scenario) -> Any:
    """Run an executable scenario in the twin; the report carries the ``gt.*`` trace hash.

    The injector applies the scripted disruptions to the ground truth and announces them on
    ``ops.disruptions`` (issue #57); a scripted entry delay holds its train at the section entry."""
    from raildss_twin import run
    from raildss_twin.observe import Observer

    if not sc.executable:
        raise ValueError(f"{sc.id} requires {', '.join(sc.requires)} and is not executed")
    world = sensitivity_world(world, sc)
    observers = [Observer(world.register, sc.degradation_profile)]
    report, _ = run.run_day(world, sc.wtt_date, observers=observers, **run_kwargs(sc, world))
    return report


def record_hash(path: Path, digest: str) -> None:
    """Write ``expected_gt_hash`` into a scenario file, keeping the rest of the file as it is."""
    text = path.read_text(encoding="utf-8")
    if not HASH_LINE.search(text):
        raise ValueError(f"{path.name}: no expected_gt_hash line")
    path.write_text(HASH_LINE.sub(f'expected_gt_hash: "{digest}"', text), encoding="utf-8", newline="\n")
