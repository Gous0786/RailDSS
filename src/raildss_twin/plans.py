"""Train plans for one simulated day, and the calibrated running times (spec FR-TWIN-06/08; issue #20).

* ``passenger_plans``: the WTT for a date (Monday-first calendar, :meth:`Timetable.runs_on`). A train's
  points on that day, ordered by sequence, become its stops and passes; times are seconds since the
  start of the simulated day (IST midnight).
* ``freight_plans``: a freight ``DayInstance``. Each load enters at its logged entry point and time and
  runs through the block stations to its exit point; the twin, not the log, decides the movement.
* :class:`Timing`: running time of a leg from the point-mass calculator plus the calibration of
  issue #16 (freight per-block allowance, passenger per-leg supplement).
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from raildss_etl.timetable import Timetable, ist
from raildss_params import Register
from raildss_schema.base import Stamped
from raildss_schema.entities import DayInstance, FreightLoad, Service, TimetablePoint
from raildss_twin.calibration import CALIBRATION_FILE, Calibration, Network, Runner
from raildss_twin.layout import Layout
from raildss_twin.rolling_stock import ConsistTemplate, RollingStock

SECONDS_PER_DAY = 86400


@dataclass(frozen=True)
class Node:
    """A point on a train's path. ``arr`` / ``dep`` are planned seconds since the start of the day."""

    op: str
    arr: float
    dep: float
    stop: bool


@dataclass
class Plan:
    run_id: str
    kind: str  # passenger | freight
    name: str
    template: ConsistTemplate
    nodes: list[Node]
    entry_s: float
    timetabled: bool  # passenger times are the booked plan; freight times are only the entry
    category: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def direction_of(self) -> str:
        return "DN" if self.nodes[0].op != self.nodes[-1].op else "-"


@dataclass(frozen=True)
class Unsupported:
    run_id: str
    reason: str


def day_start(day: dt.date) -> dt.datetime:
    """Midnight IST of ``day`` (UTC-aware), the origin of the simulation clock."""
    return dt.datetime.combine(day, dt.time(0), tzinfo=ist()).astimezone(dt.UTC)


class Timing:
    """Leg running times for the simulator (cached)."""

    def __init__(self, tables: Mapping[str, Sequence[Stamped]], register: Register) -> None:
        net = Network.from_entities(
            tables["operational_point"],  # type: ignore[arg-type]
            tables["block_section"],  # type: ignore[arg-type]
            tables["speed_profile"],  # type: ignore[arg-type]
            tables["gradient"],  # type: ignore[arg-type]
        )
        raw = json.loads(Path(CALIBRATION_FILE).read_text(encoding="utf-8"))
        self.calibration = Calibration.model_validate(raw["calibration"])
        self.runner = Runner(net, register)
        self.stock = RollingStock(register, tables["operational_point"][0].network_version)
        self.default_coaches = int(register.value("runtime.calibration")["passenger_coaches_default"])
        self._cache: dict[tuple[Any, ...], float] = {}

    def leg(self, plan: Plan, a: str, b: str, start_stopped: bool, end_stopped: bool) -> float:
        key = (plan.template.consist.id, a, b, start_stopped, end_stopped)
        if key not in self._cache:
            cal, tpl = self.calibration, plan.template
            if plan.kind == "passenger":
                cap = cal.passenger_cap_kmh
                extra = cal.leg_supplement_s.get(f"{a}-{b}", 0.0)
            else:
                cap = cal.freight_caps_kmh["loaded" if tpl.consist_type == "freight_loaded" else "empty"]
                direction = self.runner.net.direction(a, b)
                extra = cal.block_allowance_s.get(f"{a}-{b}/{direction}", 0.0)
            self._cache[key] = self.runner.time(tpl, a, b, start_stopped, end_stopped, cap) + extra
        return self._cache[key]


def passenger_plans(
    tables: Mapping[str, Sequence[Stamped]], layout: Layout, timing: Timing, day: dt.date
) -> tuple[list[Plan], list[Unsupported]]:
    services: list[Service] = list(tables["service"])  # type: ignore[arg-type]
    points: list[TimetablePoint] = list(tables["timetable_point"])  # type: ignore[arg-type]
    busy = {
        f"{p.train_id}/{p.seq}": [x == "1" for x in str(p.raw["BLOCK_BUSY_DAYS"]).split(",")] for p in points
    }
    tt = Timetable(services=services, points=points, busy_days=busy)
    plans: list[Plan] = []
    unsupported: list[Unsupported] = []
    for run in tt.runs_on(day, ist(), layout.network_version):
        svc = tt.service(str(run.train_id))
        on = sorted((p for p in tt.points_of(svc.train_id) if tt.on_block(p, day)), key=lambda p: p.seq)
        origin = run.run_date
        shift = (origin - day).days * SECONDS_PER_DAY
        if not on:
            unsupported.append(Unsupported(run.run_id, "no point of the section lies on this day"))
            continue
        # The day can cut a train inside a block, so that its first or last point of the day is a halt. Extend
        # the path to the block station before and after (their booked times may lie before or after the day);
        # a train that starts before the day is resumed inside its block at the start.
        whole = sorted(tt.points_of(svc.train_id), key=lambda p: p.seq)
        first, last = whole.index(on[0]), whole.index(on[-1])
        while first > 0 and layout.ops[whole[first].op_code].is_halt:
            first -= 1
        while last < len(whole) - 1 and layout.ops[whole[last].op_code].is_halt:
            last += 1
        on = whole[first : last + 1]
        if layout.ops[on[0].op_code].is_halt or layout.ops[on[-1].op_code].is_halt:
            unsupported.append(Unsupported(run.run_id, "the booked path itself starts or ends at a halt"))
            continue
        # arr_s / dep_s count from the start of the origin day, so they already include the day offset.
        nodes = [Node(p.op_code, shift + p.arr_s, shift + p.dep_s, p.is_stop) for p in on]
        plans.append(
            Plan(
                run_id=run.run_id,
                kind="passenger",
                name=svc.name,
                template=timing.stock.passenger(svc.coaches or timing.default_coaches),
                nodes=nodes,
                entry_s=nodes[0].arr,
                timetabled=True,
                category=svc.priority_class,
            )
        )
    return sorted(plans, key=lambda p: (p.entry_s, p.run_id)), unsupported


def freight_plans(
    tables: Mapping[str, Sequence[Stamped]], layout: Layout, timing: Timing, day_instance_id: str
) -> tuple[list[Plan], list[Unsupported], dt.date]:
    days: list[DayInstance] = list(tables["day_instance"])  # type: ignore[arg-type]
    inst = next(d for d in days if d.id == day_instance_id)
    loads = {x.load_id: x for x in tables["freight_load"] if isinstance(x, FreightLoad)}
    origin = day_start(inst.day)
    stations = layout.block_stations()
    plans: list[Plan] = []
    unsupported: list[Unsupported] = []
    for load_id in inst.run_ids:
        ld = loads[load_id]
        if ld.entry_point not in layout.ops or ld.exit_point not in layout.ops:
            unsupported.append(Unsupported(load_id, "entry or exit point is not a station of the section"))
            continue
        try:
            template = timing.stock.freight(ld.wagon_type, ld.loaded)
        except KeyError as exc:
            unsupported.append(Unsupported(load_id, str(exc)))
            continue
        i, j = stations.index(ld.entry_point), stations.index(ld.exit_point)
        path = stations[i : j + 1] if i <= j else stations[j : i + 1][::-1]
        t0 = (ld.entry_time - origin).total_seconds()
        plans.append(
            Plan(
                run_id=load_id,
                kind="freight",
                name=f"{ld.wagon_type} {'loaded' if ld.loaded else 'empty'}",
                template=template,
                nodes=[Node(op, t0, t0, False) for op in path],
                entry_s=t0,
                timetabled=False,
                category="freight",
            )
        )
    return sorted(plans, key=lambda p: (p.entry_s, p.run_id)), unsupported, inst.day
