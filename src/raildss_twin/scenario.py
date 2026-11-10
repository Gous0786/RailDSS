"""Scenario format and scenario library v0 (spec FR-SCN-01, FR-SCN-04, REC-20, §12.1; issue #21).

A scenario is a versioned, seeded YAML file (``scenarios/v0/SCN-xx.yaml``) holding the network version,
the day instance (WTT date plus a real freight day, or an NHPP seed), traffic density, degradation
profile, scripted disruptions (type, location, start, duration distribution, ETR and its uncertainty)
and the expected ground-truth trace hash.

* A scenario without disruptions is **executable**: it runs in the twin (:func:`run_scenario`) and its
  ``gt.*`` trace hash must equal ``expected_gt_hash``.
* A scenario with disruptions is tagged ``requires: [injector]`` (the injector is M1, issue #53); it is
  validated but not executed here. :meth:`ScenarioDisruption.realize` turns a disruption into the
  injector's :class:`~raildss_twin.injector.Disruption`, drawing its duration from the scenario seed.
"""

from __future__ import annotations

import datetime as dt
import random
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from raildss_etl.mapping import REPO
from raildss_twin.injector import SECONDS_PER_MIN, Disruption
from raildss_twin.sim import SECONDS_PER_HOUR

SCHEMA_VERSION = 1
NETWORK_VERSION = "KTV-PSA-2025-08"
LIBRARY_DIR = REPO / "scenarios" / "v0"
INDEX_FILE = LIBRARY_DIR / "INDEX.md"
DISRUPTION_TYPES = ("line_block", "point_failure", "signal_failure", "tsr", "loco_failure", "entry_delay")
REQUIRES = ("injector",)
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
    type: Literal["line_block", "point_failure", "signal_failure", "tsr", "loco_failure", "entry_delay"]
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="HH:MM on the scenario day")
    duration_min: Uniform
    etr: Etr | None = None
    block_id: str | None = None
    line_id: str | None = None  # None: every line of the block
    station: str | None = None
    line_no: int | None = None
    train_no: str | None = None
    speed_kmh: float | None = Field(default=None, gt=0)
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
        }[self.type]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.type} needs {', '.join(missing)}")
        return self

    @property
    def start_s(self) -> float:
        hours, minutes = self.start.split(":")
        return int(hours) * SECONDS_PER_HOUR + int(minutes) * SECONDS_PER_MIN

    def realize(self, seed: int, day: dt.date) -> Disruption | None:
        """The injector disruption for this seed; ``None`` for an ``entry_delay`` (not an injector kind)."""
        if self.type == "entry_delay":
            return None
        rng = random.Random(f"raildss-scn/{seed}/{self.incident_id}")
        duration_s = rng.uniform(self.duration_min.low_min, self.duration_min.high_min) * SECONDS_PER_MIN
        etr_s = (
            None if self.etr is None else self.start_s + duration_s + self.etr.offset_min * SECONDS_PER_MIN
        )
        run_id = f"{self.train_no}@{day.isoformat()}" if self.train_no else None
        return Disruption(
            incident_id=self.incident_id,
            kind=self.type,
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
    requires: list[Literal["injector"]] = Field(default_factory=list)
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
        if self.disruptions and "injector" not in self.requires:
            raise ValueError("a scenario with disruptions must carry requires: [injector]")
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
    return problems


def index_markdown(scenarios: Sequence[Scenario]) -> str:
    """The library index, by family."""
    out = [
        "# Scenario library v0 (KTV-PSA)",
        "",
        "Generated by `python -m raildss_twin scenario index --write` (spec FR-SCN-04, issue #21).",
        f"{len(scenarios)} scenarios. *Executable* ones run in the twin and carry the expected `gt.*` trace",
        "hash; the others are tagged `requires: injector` (issue #53) and are validated, not executed.",
    ]
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
def run_scenario(world: Any, sc: Scenario) -> Any:
    """Run an executable scenario in the twin; the report carries the ``gt.*`` trace hash."""
    from raildss_twin import run
    from raildss_twin.observe import Observer

    if not sc.executable:
        raise ValueError(f"{sc.id} requires {', '.join(sc.requires)} and is not executed")
    observers = [Observer(world.register, sc.degradation_profile)]
    report, _ = run.run_day(
        world, sc.wtt_date, seed=sc.seed, day_instance=sc.freight_day, observers=observers
    )
    return report


def record_hash(path: Path, digest: str) -> None:
    """Write ``expected_gt_hash`` into a scenario file, keeping the rest of the file as it is."""
    text = path.read_text(encoding="utf-8")
    if not HASH_LINE.search(text):
        raise ValueError(f"{path.name}: no expected_gt_hash line")
    path.write_text(HASH_LINE.sub(f'expected_gt_hash: "{digest}"', text), encoding="utf-8", newline="\n")
