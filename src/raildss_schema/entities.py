"""Canonical entities for M0 (spec §6.1, §6.6; issue #4).

Each entity declares ``__table__`` and ``__pk__``. Primary keys start with ``network_version``
so several network versions can live side by side. The DDL, migrations and JSON Schemas are
generated from these classes (``python -m raildss_schema generate``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any, ClassVar, Literal

from pydantic import Field

from raildss_schema.base import Location, SchemaModel, Stamped, UtcDatetime, WeekMask

Direction = Literal["DN", "UP"]
LineDirections = Literal["DN", "UP", "BOTH"]
BlockSystem = Literal["ABS_absolute", "AUTO", "IBS"]
DataSource = Literal["data", "synthetic", "dummy"]

# ------------------------------------------------------------------ infrastructure (meso / macro)


class NetworkVersion(Stamped):
    __table__: ClassVar[str] = "network_version"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version",)

    created_at: UtcDatetime
    description: str
    source_hashes: dict[str, str] = Field(default_factory=dict)
    validator_report: dict[str, Any] | None = None
    dq_report: dict[str, Any] | None = None


class OperationalPoint(Stamped):
    """Station / halt / junction (STATION, RouteSttnInfo). PostGIS ``geom`` is generated."""

    __table__: ClassVar[str] = "operational_point"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "code")

    code: str = Field(min_length=1)
    name: str
    seq: int = Field(ge=1)
    km: float = Field(description="section chainage from KTV")
    km_ref_hwh: float | None = None
    lat: float | None = None
    lon: float | None = None
    type: Literal["block_station_A", "block_station_B", "block_station_C", "halt", "ibh", "cabin"]
    is_junction: bool = False
    is_halt: bool = False
    is_cabin: bool = False
    is_boundary: bool = False
    signalling: str | None = None
    interlocking_std: str | None = None
    block_instrument: str | None = None
    crossing_facility: bool = False
    traction_change: bool = False
    crew_change: bool = False
    kavach: bool = False
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None


class BlockSection(Stamped):
    """Directed block section ``A-B`` at COA level or route level split at a halt (REC-26)."""

    __table__: ClassVar[str] = "block_section"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id", "level")

    id: str = Field(min_length=3)
    from_op: str
    to_op: str
    level: Literal["coa", "route_split_at_halt"]
    direction: Direction
    n_lines: int = Field(ge=1)
    system: BlockSystem
    length_m: float = Field(gt=0)
    vmax_kmh: float = Field(gt=0)
    adequate_distance_m: float | None = None
    line_clear_op_time_s: float | None = None
    controller_section: str | None = None


class BlockSectionLine(Stamped):
    __table__: ClassVar[str] = "block_section_line"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "block_section_id", "line_id")

    block_section_id: str
    line_id: str = Field(description="DN / UP / MID / KK / 1")
    direction: LineDirections
    length_m: float = Field(gt=0)
    system: BlockSystem
    vmax_kmh: float = Field(gt=0)
    category: str | None = None


class StationLine(Stamped):
    __table__: ClassVar[str] = "station_line"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "op_code", "line_no")

    op_code: str
    line_no: int = Field(ge=1)
    name: str | None = None
    kind: Literal["main", "loop", "siding", "yard"]
    csr_m: float | None = Field(default=None, ge=0)
    csr_status: Literal["data", "estimated"] = "data"
    csr_low_m: float | None = None
    csr_high_m: float | None = None
    length_m: float | None = None
    line_speed_kmh: float | None = None
    directions: LineDirections
    capacity_trains: int = Field(default=1, ge=1)
    platform: bool = False
    passenger_ok: bool = True
    goods_ok: bool = True
    electrified: bool | None = None
    status: Literal["in_use", "out_of_use"] = "in_use"


class ConnectionEdge(Stamped):
    """Station line receives from / sends to a block-section line (COA Connections; REC-07)."""

    __table__: ClassVar[str] = "connection_edge"
    __pk__: ClassVar[tuple[str, ...]] = (
        "network_version",
        "op_code",
        "station_line_no",
        "block_section_id",
        "block_line_id",
        "mode",
    )

    op_code: str
    station_line_no: int = Field(ge=1)
    block_section_id: str
    block_line_id: str
    mode: Literal["receive", "send"]


class Platform(Stamped):
    __table__: ClassVar[str] = "platform"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "op_code", "platform_no")

    op_code: str
    platform_no: str
    station_line_no: int | None = None
    length_m: float | None = Field(default=None, ge=0)
    platform_type: Literal["high", "low", "rail"] | None = None
    side: Literal["UP", "DN"] | None = None


class SpeedProfile(Stamped):
    """Permanent / temporary / sectional speed restriction over a range."""

    __table__: ClassVar[str] = "speed_profile"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    kind: Literal["PSR", "TSR", "sectional"]
    direction: Direction | None = None
    block_section_id: str | None = None
    line_id: str | None = None
    location_from: Location
    location_to: Location
    vmax_passenger_kmh: float = Field(gt=0)
    vmax_goods_kmh: float = Field(gt=0)
    length_m: float | None = None
    reason: str | None = None
    time_loss_passenger_s: float | None = None
    time_loss_goods_s: float | None = None
    stop_dead: bool = False
    source: DataSource = "data"


class Gradient(Stamped):
    __table__: ClassVar[str] = "gradient"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    block_section_id: str
    from_km: float
    to_km: float
    gradient_permille: float = Field(description="positive = rising in the DN direction")
    source: DataSource = "synthetic"


class Curve(Stamped):
    __table__: ClassVar[str] = "curve"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    block_section_id: str
    from_km: float
    to_km: float
    degree: float | None = None
    radius_m: float | None = None
    source: DataSource = "synthetic"


class Signal(Stamped):
    """Twin-only signal (synthesised; the dataset has no signal positions, R06 §4.4)."""

    __table__: ClassVar[str] = "signal"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    type: Literal["distant", "home", "starter", "advanced_starter", "automatic", "ib", "calling_on", "shunt"]
    direction: Direction
    location: Location
    op_code: str | None = None
    block_section_id: str | None = None
    line_id: str | None = None
    aspects: int | None = Field(default=None, ge=2)
    source: DataSource = "synthetic"


class SubBlock(Stamped):
    """Automatic-signal sub-block on a block line (KTV-VZM; spec FR-TWIN-04)."""

    __table__: ClassVar[str] = "sub_block"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "block_section_id", "line_id", "seq")

    block_section_id: str
    line_id: str
    seq: int = Field(ge=1)
    from_offset_m: float = Field(ge=0)
    to_offset_m: float = Field(gt=0)
    entry_signal_id: str | None = None
    source: DataSource = "synthetic"


# ------------------------------------------------------------------ rolling stock


class LocoClass(Stamped):
    __table__: ClassVar[str] = "loco_class"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "code")

    code: str
    traction: Literal["electric", "diesel"]
    mass_t: float = Field(gt=0)
    length_m: float = Field(gt=0)
    vmax_kmh: float = Field(gt=0)
    power_kw: float | None = None
    te_start_kn: float | None = None
    te_curve: list[tuple[float, float]] = Field(default_factory=list, description="(speed km/h, TE kN)")
    davis_a: float | None = None
    davis_b: float | None = None
    davis_c: float | None = None
    brake_decel_mps2: float | None = None


class VehicleType(Stamped):
    __table__: ClassVar[str] = "vehicle_type"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "code")

    code: str
    kind: Literal["coach", "wagon"]
    length_m: float = Field(gt=0)
    tare_t: float | None = None
    gross_t: float | None = None
    vmax_kmh: float | None = None


class ConsistVehicle(SchemaModel):
    vehicle_type: str
    count: int = Field(ge=1)


class Consist(Stamped):
    __table__: ClassVar[str] = "consist"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    template: bool = False
    loco_codes: list[str] = Field(min_length=1)
    vehicles: list[ConsistVehicle] = Field(default_factory=list)
    length_m: float = Field(gt=0)
    trailing_t: float | None = None
    vmax_kmh: float | None = None
    brake_pct: float | None = None


# ------------------------------------------------------------------ timetable and demand


class Service(Stamped):
    """Passenger train version (TRAIN table)."""

    __table__: ClassVar[str] = "service"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "train_id")

    train_id: str = Field(description="TRAINID of the train version")
    train_no: str
    name: str
    icms_train_type: str
    priority_class: str
    days: WeekMask
    valid_from: dt.date
    valid_to: dt.date
    coaches: int | None = Field(default=None, ge=1)
    reverse_train_no: str | None = None
    forecolor: str | None = None


class TimetablePoint(Stamped):
    """Train x station row of the WTT (SCHEDULE). Times in seconds since 00:00 of day 1."""

    __table__: ClassVar[str] = "timetable_point"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "train_id", "seq")

    train_id: str
    seq: int = Field(ge=1)
    op_code: str
    arr_s: int = Field(ge=0)
    dep_s: int = Field(ge=0)
    day_offset: int = Field(ge=0)
    is_stop: bool
    direction: Direction | None = None
    block_section_id: str | None = None
    coa_block_section_id: str | None = None
    eng_allowance_s: int = 0
    traffic_allowance_s: int = 0
    acc_s: int = 0
    dec_s: int = 0
    planned_line: list[str | None] = Field(default_factory=list, description="per weekday, Monday-first")
    booked_crossing_train: str | None = None
    crew_change: str | None = None
    traction_change: str | None = None
    reversal: bool = False


class TrainRun(Stamped):
    """Daily instance of a passenger service or a freight load (``train_no@date`` / ``FRT-<LoadId>``)."""

    __table__: ClassVar[str] = "train_run"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "run_id")

    run_id: str
    kind: Literal["passenger", "freight"]
    run_date: dt.date
    train_id: str | None = None
    load_id: str | None = None
    consist_id: str | None = None
    entry_point: str
    exit_point: str
    entry_time: UtcDatetime | None = None
    entry_delay_s: int | None = None
    status: Literal["planned", "running", "completed", "cancelled"] = "planned"


class FreightLoad(Stamped):
    """One load from the freight log (WAT goods train movements)."""

    __table__: ClassVar[str] = "freight_load"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "load_id")

    load_id: str
    rake_id: str | None = None
    wagon_type: str
    loaded: bool
    commodity: str | None = None
    origin: str | None = None
    destination: str | None = None
    entry_point: str
    exit_point: str
    direction: Direction
    entry_time: UtcDatetime
    exit_time: UtcDatetime | None = None
    path: list[str] = Field(default_factory=list, description="block sections in order")
    stabled: bool = False
    consist_template: str | None = None


class DayInstance(Stamped):
    __table__: ClassVar[str] = "day_instance"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    day: dt.date
    freight_source: Literal["real_day", "nhpp"]
    freight_seed: int | None = None
    run_ids: list[str] = Field(default_factory=list)


class ScenarioDefinition(Stamped):
    __table__: ClassVar[str] = "scenario_definition"
    __pk__: ClassVar[tuple[str, ...]] = ("network_version", "id")

    id: str
    family: str
    seed: int
    day_instance_id: str
    density: float = Field(default=1.0, gt=0)
    degradation_profile: Literal["D0", "D1", "D2", "D3"] = "D1"
    disruptions: list[dict[str, Any]] = Field(default_factory=list)
    gt_hash: str | None = None
    requires: list[str] = Field(default_factory=list)


ENTITIES: tuple[type[Stamped], ...] = (
    NetworkVersion,
    OperationalPoint,
    BlockSection,
    BlockSectionLine,
    StationLine,
    ConnectionEdge,
    Platform,
    SpeedProfile,
    Gradient,
    Curve,
    Signal,
    SubBlock,
    LocoClass,
    VehicleType,
    Consist,
    Service,
    TimetablePoint,
    TrainRun,
    FreightLoad,
    DayInstance,
    ScenarioDefinition,
)
