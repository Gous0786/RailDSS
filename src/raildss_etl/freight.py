"""ETL stage 6: freight demand and historical statistics (issue #11).

Spec FR-TWIN-08, FR-KPI-11, DQ-21, REC-25.

From the Aug 2025 freight log (division-wide, stage 1):

* block times are recomputed from timestamps (arrival at B minus departure from the previous
  station); the log's ``Block Km`` and ``Speed`` are never read (DQ-21). ``Block Hrs`` (a day
  fraction) is used only to check the recomputed times;
* ``FreightLoad`` per load that uses the section: entry / exit station, direction (from station
  chainage, since some log blocks skip stations), in-section path, times in UTC, stabled flag,
  consist template id (templates themselves are issue #15);
* per adjacent block, direction and loaded/empty: run-time quantiles (p10 = unimpeded proxy);
* 27 ``DayInstance`` replay days and a fitted NHPP (hourly rate by entry point and direction);
* historical reference statistics (BL-5): through-transit, dwell by station, loads per day.

Definitions that reproduce R06 §2: through-transit = last minus first arrival at section stations,
for loads calling at >= 15 section stations; loads per day = distinct loads per adjacent block per
day (R06's figures match the busiest block of each stretch and direction).
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any, cast

import numpy as np
import pandas as pd
from pydantic import BaseModel

from raildss_etl.mapping import MappingSpec, load_mapping
from raildss_etl.topology import Codec, Topology
from raildss_params import Register, load
from raildss_schema.entities import DayInstance, FreightLoad

MINUTES_PER_DAY = 1440
HOURS_PER_DAY = 24


class BlockTime(BaseModel):
    block: str
    direction: str
    loaded: bool
    n: int
    p10_min: float
    p50_min: float
    p90_min: float


class Stage6Report(BaseModel):
    section_rows: int
    loads: int
    block_hrs_agreement: float
    block_hrs_checked_rows: int
    loads_per_day_by_block: dict[str, float]  # "stretch/dir/block" -> mean distinct loads per day
    busiest_block_loads_per_day: dict[str, float]  # "stretch/dir" -> max over adjacent blocks
    through_transit_h: dict[str, float]  # median, p10, p90, n
    dwell_min: dict[str, dict[str, float]]  # station -> median, mean, p90, share_over_30, n
    stabled_loads: int
    days: int


class FreightDemand(BaseModel):
    loads: list[FreightLoad]
    block_times: list[BlockTime]
    days: list[DayInstance]
    nhpp_rate_per_h: dict[str, list[float]]  # "entry/dir" -> 24 hourly mean arrival rates

    def sample(self, seed: int) -> list[tuple[str, str, int]]:
        """One synthetic day: (entry point, direction, minute of day) per arrival."""
        rng = np.random.default_rng(seed)
        out: list[tuple[str, str, int]] = []
        for key, rates in sorted(self.nhpp_rate_per_h.items()):
            entry, direction = key.split("/")
            for hour, rate in enumerate(rates):
                for _ in range(int(rng.poisson(rate))):
                    out.append((entry, direction, hour * 60 + int(rng.integers(0, 60))))
        return sorted(out, key=lambda x: x[2])


def _q(values: pd.Series, q: float) -> float:
    return round(float(values.quantile(q)), 2)


def build_freight(
    tables: dict[str, pd.DataFrame],
    topology: Topology,
    mapping: MappingSpec | None = None,
    register: Register | None = None,
) -> tuple[FreightDemand, Stage6Report]:
    spec = mapping or load_mapping()
    reg = register or load()
    codec = Codec(spec)
    nv = str(reg.value("network.version"))
    km = {o.code: o.km for o in topology.operational_points}
    vzm = km["VZM"]
    adjacent = {b.id for b in topology.block_sections}
    stabled_after = float(reg.value("freight.stabled_threshold_min"))
    min_stations = int(reg.value("freight.through_transit_min_stations"))
    long_dwell = float(reg.value("kpi.freight_long_dwell_min"))

    f = tables["FREIGHT"].copy()
    f["_prev_dep"] = f.groupby("LoadId", sort=False)["Depart Time"].shift()
    f["_blk_min"] = (f["Arrival Time"] - f["_prev_dep"]).dt.total_seconds() / 60
    f["_dwell_min"] = (f["Depart Time"] - f["Arrival Time"]).dt.total_seconds() / 60
    block_hrs = pd.to_numeric(f["Block Hrs"], errors="coerce") * MINUTES_PER_DAY
    checked = f["_blk_min"].notna() & block_hrs.notna()
    agree = float(((f["_blk_min"] - block_hrs).abs() <= 1)[checked].mean())

    ends = f["Block Section"].astype(str).str.split("-")
    in_section = ends.map(lambda e: len(e) == 2 and e[0] in km and e[1] in km)
    s = f[in_section].copy()
    pairs = s["Block Section"].str.split("-")
    s["_dir"] = [("DN" if km[b] > km[a] else "UP") for a, b in pairs]
    s["_stretch"] = [("ktv_vzm" if max(km[a], km[b]) <= vzm else "vzm_psa") for a, b in pairs]
    s["_from"] = [a for a, _ in pairs]
    s["_day"] = s["Arrival Time"].dt.date

    # ---------------------------------------------------------------- loads
    loads: list[FreightLoad] = []
    local_entry: dict[str, dt.datetime] = {}  # IST, for day and hour bucketing
    for load_id, rows in s.groupby("LoadId", sort=False):
        first, last = rows.iloc[0], rows.iloc[-1]
        start = first["_prev_dep"] if pd.notna(first["_prev_dep"]) else first["Arrival Time"]
        local_entry[str(load_id)] = start.to_pydatetime()
        at_section = f.loc[(f["LoadId"] == load_id) & f["Sttn"].isin(km.keys()), "_dwell_min"]
        loads.append(
            FreightLoad(
                network_version=nv,
                load_id=str(load_id),
                rake_id=codec.text(first["RakeId"]),
                wagon_type=str(first["Load Type"]),
                loaded=bool(codec.decode("loaded_empty", first["LE"])),
                commodity=codec.text(first["Commodity"]),
                origin=codec.text(first["Source"]),
                destination=codec.text(first["Destination"]),
                entry_point=str(first["_from"]),
                exit_point=str(last["Sttn"]),
                direction=rows["_dir"].mode().iloc[0],
                entry_time=start.to_pydatetime(),
                exit_time=last["Arrival Time"].to_pydatetime(),
                path=[str(b) for b in rows["Block Section"]],
                stabled=bool((at_section > stabled_after).any()),
                consist_template=f"TPL-{first['Load Type']}",
            )
        )

    # ---------------------------------------------------------------- block times (adjacent blocks)
    adj = s[s["Block Section"].isin(adjacent) & s["_blk_min"].notna() & (s["_blk_min"] >= 0)]
    adj = adj.assign(_loaded=adj["LE"].map(lambda v: bool(codec.decode("loaded_empty", v))))
    block_times = [
        BlockTime(
            block=str(b),
            direction=str(d),
            loaded=bool(ld),
            n=len(g),
            p10_min=_q(g["_blk_min"], 0.1),
            p50_min=_q(g["_blk_min"], 0.5),
            p90_min=_q(g["_blk_min"], 0.9),
        )
        for (b, d, ld), g in adj.groupby(["Block Section", "_dir", "_loaded"])
    ]

    # ---------------------------------------------------------------- days and NHPP
    entry_day = {lid: ts.date() for lid, ts in local_entry.items()}
    by_day: dict[dt.date, list[str]] = defaultdict(list)
    for lid, day in entry_day.items():
        by_day[day].append(lid)
    days = [
        DayInstance(
            network_version=nv,
            id=f"aug25-day-{day.day:02d}",
            day=day,
            freight_source="real_day",
            run_ids=sorted(ids),
        )
        for day, ids in sorted(by_day.items())
    ]
    n_days = len(days)
    counts: dict[str, list[float]] = defaultdict(lambda: [0.0] * HOURS_PER_DAY)
    for ld in loads:
        counts[f"{ld.entry_point}/{ld.direction}"][local_entry[ld.load_id].hour] += 1
    rates = {k: [c / n_days for c in v] for k, v in sorted(counts.items())}

    # ---------------------------------------------------------------- historical statistics
    per_block = (
        s[s["Block Section"].isin(adjacent)]
        .groupby(["_stretch", "_dir", "Block Section", "_day"])["LoadId"]
        .nunique()
        .groupby(level=[0, 1, 2])
        .mean()
    )
    by_block: dict[str, float] = {}
    busiest: dict[str, float] = {}
    for key, value in per_block.to_dict().items():
        stretch, direction, block = cast(tuple[str, str, str], key)
        by_block[f"{stretch}/{direction}/{block}"] = round(float(value), 2)
        group = f"{stretch}/{direction}"
        busiest[group] = max(busiest.get(group, 0.0), round(float(value), 2))
    calls = f[f["Sttn"].isin(km.keys())]
    g = calls.groupby("LoadId")
    transit = (g["Arrival Time"].max() - g["Arrival Time"].min())[g["Sttn"].nunique() >= min_stations]
    hours = transit.dt.total_seconds() / 3600
    dwell: dict[str, dict[str, float]] = {}
    for station in ("VZM", "KTV", "NWP"):
        d = f.loc[f["Sttn"] == station, "_dwell_min"].dropna()
        dwell[station] = {
            "median": _q(d, 0.5),
            "mean": round(float(d.mean()), 1),
            "p90": _q(d, 0.9),
            "share_over_30": round(float((d > long_dwell).mean()), 3),
            "n": float(len(d)),
        }
    report = Stage6Report(
        section_rows=len(s),
        loads=len(loads),
        block_hrs_agreement=round(agree, 4),
        block_hrs_checked_rows=int(checked.sum()),
        loads_per_day_by_block=by_block,
        busiest_block_loads_per_day=busiest,
        through_transit_h={
            "median": _q(hours, 0.5),
            "p10": _q(hours, 0.1),
            "p90": _q(hours, 0.9),
            "n": float(len(hours)),
        },
        dwell_min=dwell,
        stabled_loads=sum(1 for ld in loads if ld.stabled),
        days=n_days,
    )
    return FreightDemand(loads=loads, block_times=block_times, days=days, nhpp_rate_per_h=rates), report


def report_dict(report: Stage6Report) -> dict[str, Any]:
    return report.model_dump(mode="json")


def rake_length_m(wagon_type: str, register: Register | None = None) -> float | None:
    """Rake + loco length from the register's rake templates; None until issue #15 adds the type."""
    reg = register or load()
    rake = reg.value("consist.freight.rake").get(wagon_type)
    if rake is None:
        return None
    return float(rake["wagons"] * rake["wagon_length_m"] + reg.value("consist.loco_length_m"))


def write_freight(demand: FreightDemand, report: Stage6Report, out_dir: Any) -> None:
    import json

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, items in (
        ("freight_loads", demand.loads),
        ("day_instances", demand.days),
        ("block_times", demand.block_times),
    ):
        lines = [json.dumps(i.model_dump(mode="json"), sort_keys=True) for i in items]
        (out_dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {"report": report_dict(report), "nhpp_rate_per_h": demand.nhpp_rate_per_h}
    (out_dir / "freight_report.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
