"""Run one real day in the twin (issue #20): load the published network, plan, simulate.

``run_day`` is the entry point used by the CLI and the tests. The passenger plan is the WTT for the
date; the freight plan is a ``DayInstance`` (default: the real freight day of the same date).
"""

from __future__ import annotations

import datetime as dt
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import simpy

from raildss_etl import publish
from raildss_params import Register, load
from raildss_schema.base import Stamped
from raildss_twin.clock import Clock
from raildss_twin.layout import Layout, build_layout
from raildss_twin.plans import Plan, Timing, Unsupported, day_start, freight_plans, passenger_plans
from raildss_twin.sim import RunReport, Sim, Sink

DAY_INSTANCE_PREFIX = "aug25-day-"


@dataclass
class World:
    """Everything loaded once: published tables, layout and running-time model."""

    tables: Mapping[str, Sequence[Stamped]]
    layout: Layout
    timing: Timing
    register: Register


def load_world(network_version: str | None = None, register: Register | None = None) -> World:
    reg = register or load()
    version = network_version or str(reg.value("network.version"))
    _, tables, _ = publish.read_export(publish.default_dir(version))
    return World(tables, build_layout(tables), Timing(tables, reg), reg)


def plans_for(
    world: World, day: dt.date, day_instance: str | None = None
) -> tuple[list[Plan], list[Unsupported], str]:
    inst = day_instance or f"{DAY_INSTANCE_PREFIX}{day.day:02d}"
    pax, bad_pax = passenger_plans(world.tables, world.layout, world.timing, day)
    frt, bad_frt, inst_day = freight_plans(world.tables, world.layout, world.timing, inst)
    if inst_day != day:
        raise ValueError(f"day instance {inst} is for {inst_day}, not {day}")
    return sorted(pax + frt, key=lambda p: (p.entry_s, p.run_id)), bad_pax + bad_frt, inst


def run_day(
    world: World,
    day: dt.date,
    *,
    seed: int | None = None,
    day_instance: str | None = None,
    sink: Sink | None = None,
    clock: Clock | None = None,
    until_s: float | None = None,
    keep_events: bool = False,
    plans: list[Plan] | None = None,
    scenario_id: str | None = None,
) -> tuple[RunReport, Sim]:
    reg = world.register
    seed = int(reg.value("twin.sim")["default_seed"]) if seed is None else seed
    unsupported: list[Unsupported] = []
    inst = day_instance or f"{DAY_INSTANCE_PREFIX}{day.day:02d}"
    if plans is None:
        plans, unsupported, inst = plans_for(world, day, day_instance)
    env: simpy.Environment = clock.environment() if clock else simpy.Environment()
    sim = Sim(
        world.layout,
        world.timing,
        reg,
        plans,
        day,
        origin=day_start(day),
        scenario_id=scenario_id or f"{inst}-bl0",
        seed=seed,
        sink=sink,
        env=env,
        until_s=until_s,
        keep_events=keep_events,
    )
    t0 = time.perf_counter()
    report = sim.run()
    report.wall_s = round(time.perf_counter() - t0, 2)
    report.unsupported = [{"run_id": u.run_id, "reason": u.reason} for u in unsupported]
    return report, sim


def summary(report: RunReport) -> dict[str, Any]:
    return report.model_dump(mode="json", exclude={"stuck", "deadlocks", "unsupported"})
