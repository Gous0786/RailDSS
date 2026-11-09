"""Historical reference KPIs from the Aug 2025 freight log (spec FR-KPI-11, §10, BL-5; issue #80).

Per day and over the month, for the KTV-PSA section:

* **Through transit** (hours): last minus first arrival at section stations, for loads that call at
  at least ``freight.through_transit_min_stations`` section stations (median, p10, p90). A load
  belongs to the day it enters the section (the day instance of ``KTV-PSA-2025-08``).
* **Dwell** by station (minutes): median, mean, p90 and the share above ``kpi.freight_long_dwell_min``.
  A call belongs to the day it arrives.
* **Loads per day per block**: distinct loads on each adjacent block section, by arrival day, and the
  busiest block per stretch and direction (the R06 figures).
* **Delay indicators the log supports**: detention (dwell beyond the long-dwell threshold, hours),
  calls and loads detained, stabled loads (a stop longer than ``freight.stabled_threshold_min``), and
  running-time excess (block run time above the log p10 of its block, direction and loaded / empty,
  the unimpeded proxy of issue #16).

The log has no passenger actuals, so no passenger delay indicator exists here. The month figures use
the same definitions as ETL stage 6 and reproduce its report (R06 §2). They are computed from the
division freight log through ETL stages 1, 2 and 6 and cross-checked against the freight loads and day
instances of the published network: the publication carries entry and exit per load but not each call,
so dwell cannot be recomputed from it alone.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

import pandas as pd

from raildss_etl.freight import FreightDemand, Stage6Report
from raildss_etl.mapping import REPO, MappingSpec
from raildss_etl.topology import Codec, Topology
from raildss_params import Register

OUT_DIR = REPO / "docs" / "kpi"
JSON_FILE = "historical-reference-ktv-psa.json"
MD_FILE = "historical-reference-ktv-psa.md"
FORMAT_VERSION = "1"
MINUTES_PER_HOUR = 60
SHOWN_STATIONS = ("KTV", "VZM", "NWP")  # dwell shown per day in the Markdown (all stations are in the JSON)


def _q(values: pd.Series, q: float) -> float:
    return round(float(values.quantile(q)), 2)


def _dwell_stats(d: pd.Series, long_dwell: float) -> dict[str, float]:
    return {
        "median": _q(d, 0.5),
        "mean": round(float(d.mean()), 1),
        "p90": _q(d, 0.9),
        "share_over_long": round(float((d > long_dwell).mean()), 3),
        "n": float(len(d)),
    }


def _transit_stats(hours: pd.Series) -> dict[str, float]:
    return {"median": _q(hours, 0.5), "p10": _q(hours, 0.1), "p90": _q(hours, 0.9), "n": float(len(hours))}


def build_reference(
    tables: dict[str, pd.DataFrame],
    topology: Topology,
    demand: FreightDemand,
    stage6: Stage6Report,
    mapping: MappingSpec,
    register: Register,
) -> dict[str, Any]:
    codec = Codec(mapping)
    km = {o.code: o.km for o in topology.operational_points}
    adjacent = {b.id for b in topology.block_sections}
    vzm = km["VZM"]
    min_stations = int(register.value("freight.through_transit_min_stations"))
    long_dwell = float(register.value("kpi.freight_long_dwell_min"))
    stabled_after = float(register.value("freight.stabled_threshold_min"))
    entry_day = {rid: d.day for d in demand.days for rid in d.run_ids}
    stabled = {ld.load_id for ld in demand.loads if ld.stabled}
    p10 = {(b.block, b.direction, b.loaded): b.p10_min for b in demand.block_times}

    f = tables["FREIGHT"].copy()
    f["_prev_dep"] = f.groupby("LoadId", sort=False)["Depart Time"].shift()
    f["_blk_min"] = (f["Arrival Time"] - f["_prev_dep"]).dt.total_seconds() / MINUTES_PER_HOUR
    f["_dwell_min"] = (f["Depart Time"] - f["Arrival Time"]).dt.total_seconds() / MINUTES_PER_HOUR
    f["_day"] = f["Arrival Time"].dt.date
    calls = f[f["Sttn"].isin(km.keys())].copy()

    # ---- through transit
    g = calls.groupby("LoadId")
    transit_h = ((g["Arrival Time"].max() - g["Arrival Time"].min()).dt.total_seconds() / 3600)[
        g["Sttn"].nunique() >= min_stations
    ]
    transit_day = transit_h.groupby(transit_h.index.map(lambda lid: entry_day.get(str(lid))))
    transit = {
        "month": _transit_stats(transit_h),
        "by_day": {str(d): _transit_stats(v) for d, v in transit_day if d is not None},
    }

    # ---- dwell by station
    dwell_calls = calls.dropna(subset=["_dwell_min"])
    dwell = {
        "month": {
            st: _dwell_stats(v["_dwell_min"], long_dwell) for st, v in sorted(dwell_calls.groupby("Sttn"))
        },
        "by_day": {
            str(day): {
                st: _dwell_stats(v["_dwell_min"], long_dwell) for st, v in sorted(rows.groupby("Sttn"))
            }
            for day, rows in sorted(dwell_calls.groupby("_day"))
        },
    }

    # ---- loads per day per block (arrival day, as the R06 figures)
    ends = f["Block Section"].astype(str).str.split("-")
    in_section = ends.map(lambda e: len(e) == 2 and e[0] in km and e[1] in km)
    s = f[in_section & f["Block Section"].isin(adjacent)].copy()
    pairs = s["Block Section"].str.split("-")
    s["_dir"] = [("DN" if km[b] > km[a] else "UP") for a, b in pairs]
    s["_stretch"] = [("ktv_vzm" if max(km[a], km[b]) <= vzm else "vzm_psa") for a, b in pairs]
    s["_key"] = s["_stretch"] + "/" + s["_dir"] + "/" + s["Block Section"].astype(str)
    per_day = s.groupby(["_key", "_day"])["LoadId"].nunique()
    loads_by_block: dict[str, dict[str, int]] = defaultdict(dict)
    for key_day, n in per_day.to_dict().items():
        block_key, day = cast(tuple[str, Any], key_day)
        loads_by_block[block_key][str(day)] = int(n)
    month_mean = {k: round(sum(v.values()) / len(v), 2) for k, v in sorted(loads_by_block.items())}
    busiest_month: dict[str, float] = {}
    busiest_day: dict[str, dict[str, int]] = defaultdict(dict)
    for key, block_days in loads_by_block.items():
        group = "/".join(key.split("/")[:2])
        busiest_month[group] = max(busiest_month.get(group, 0.0), month_mean[key])
        for day, n in block_days.items():
            busiest_day[group][day] = max(busiest_day[group].get(day, 0), n)
    loads = {
        "loads_per_day_by_block": month_mean,
        "busiest_block_loads_per_day": dict(sorted(busiest_month.items())),
        "by_day_by_block": {k: dict(sorted(v.items())) for k, v in sorted(loads_by_block.items())},
        "busiest_block_by_day": {k: dict(sorted(v.items())) for k, v in sorted(busiest_day.items())},
    }

    # ---- delay indicators
    detained = dwell_calls[dwell_calls["_dwell_min"] > long_dwell]
    s["_loaded"] = s["LE"].map(lambda v: bool(codec.decode("loaded_empty", v)))
    runs = s[s["_blk_min"].notna() & (s["_blk_min"] >= 0)].copy()
    runs["_ref"] = [
        p10.get((b, d, ld))
        for b, d, ld in zip(runs["Block Section"], runs["_dir"], runs["_loaded"], strict=True)
    ]
    runs = runs.dropna(subset=["_ref"])
    runs["_excess"] = (runs["_blk_min"] - runs["_ref"]).clip(lower=0)
    all_days = sorted({str(d) for d in f["_day"].unique()} | {str(d.day) for d in demand.days})
    by_day: dict[str, dict[str, float]] = {}
    for day in all_days:
        d_det = detained[detained["_day"].astype(str) == day]
        d_run = runs[runs["_day"].astype(str) == day]
        by_day[day] = {
            "detention_hours": round(float((d_det["_dwell_min"] - long_dwell).sum()) / MINUTES_PER_HOUR, 2),
            "detained_calls": float(len(d_det)),
            "detained_loads": float(d_det["LoadId"].nunique()),
            "stabled_loads": float(
                sum(1 for lid, dd in entry_day.items() if str(dd) == day and lid in stabled)
            ),
            "running_excess_hours": round(float(d_run["_excess"].sum()) / MINUTES_PER_HOUR, 2),
            "running_excess_mean_min": round(float(d_run["_excess"].mean()), 2) if len(d_run) else 0.0,
            "block_runs": float(len(d_run)),
        }
    delay = {
        "definitions": {
            "detention_hours": f"sum over section calls of (dwell - {long_dwell:g} min), where positive",
            "stabled_loads": f"loads with a section stop longer than {stabled_after:g} min",
            "running_excess": "block run time above the p10 of its block, direction and loaded/empty",
        },
        "month": {
            "detention_hours": round(sum(v["detention_hours"] for v in by_day.values()), 2),
            "detained_calls": sum(v["detained_calls"] for v in by_day.values()),
            "stabled_loads": float(len(stabled)),
            "running_excess_hours": round(sum(v["running_excess_hours"] for v in by_day.values()), 2),
            "running_excess_mean_min": round(float(runs["_excess"].mean()), 2),
            "block_runs": float(len(runs)),
        },
        "by_day": by_day,
    }

    return {
        "format_version": FORMAT_VERSION,
        "dataset": "KTV-PSA freight log, 2025-08-01 to 2025-08-27",
        "network_version": demand.loads[0].network_version,
        "loads": stage6.loads,
        "days": [str(d.day) for d in demand.days],
        "loads_by_day": {str(d.day): len(d.run_ids) for d in demand.days},
        "parameters": {
            "through_transit_min_stations": min_stations,
            "long_dwell_min": long_dwell,
            "stabled_threshold_min": stabled_after,
        },
        "definitions": {
            "through_transit": "last minus first arrival at section stations, loads calling at >= min "
            "stations; "
            "a load belongs to the day it enters the section",
            "dwell": "departure minus arrival at section stations; a call belongs to its arrival day",
            "loads_per_day": "distinct loads per adjacent block section by arrival day; month figure is the "
            "mean "
            "over days with traffic on the block",
            "passenger_delay": "not available: the log has no passenger actuals",
        },
        "through_transit_h": transit,
        "dwell_min": dwell,
        "loads_per_day": loads,
        "delay_indicators": delay,
    }


def to_json(reference: dict[str, Any]) -> str:
    return json.dumps(reference, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def _table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    return [
        "| " + " | ".join(headers) + " |",
        "|" + "---|" * len(headers),
        *["| " + " | ".join(str(c) for c in r) + " |" for r in rows],
    ]


def to_markdown(r: dict[str, Any]) -> str:
    t, dw, ld, de = r["through_transit_h"], r["dwell_min"], r["loads_per_day"], r["delay_indicators"]
    m = t["month"]
    out = [
        "# Historical reference KPIs, KTV-PSA freight (FR-KPI-11, BL-5)",
        "",
        "Generated by `python -m raildss_etl kpi-reference --write` (issue #80). No timestamps.",
        "",
        f"{r['dataset']} · {r['loads']} loads on {len(r['days'])} days · network `{r['network_version']}`.",
        "Values are the reference the twin's baseline and the DSS are compared with in real-day replays.",
        "",
        "## Month",
        "",
        f"**Through transit** (loads calling at {r['parameters']['through_transit_min_stations']} or more "
        "section "
        f"stations, n = {m['n']:g}): median **{m['median']} h**, p10 {m['p10']} h, p90 {m['p90']} h.",
        "",
        "**Dwell by station** (minutes):",
        "",
        *_table(
            ["Station", "Median", "Mean", "p90", "Share over long", "Calls"],
            [
                [st, v["median"], v["mean"], v["p90"], f"{v['share_over_long']:.1%}", f"{v['n']:g}"]
                for st, v in dw["month"].items()
            ],
        ),
        "",
        "**Busiest block, loads per day** (mean over days with traffic):",
        "",
        *_table(
            ["Stretch / direction", "Loads per day"],
            [[k, v] for k, v in ld["busiest_block_loads_per_day"].items()],
        ),
        "",
        "**Delay indicators:**",
        "",
        *_table(["Indicator", "Month"], [[k, f"{v:g}"] for k, v in de["month"].items()]),
        "",
        *[f"- `{k}`: {v}" for k, v in de["definitions"].items()],
        "",
        "## Per day",
        "",
        *_table(
            [
                "Day",
                "Loads",
                "Transit median (h)",
                "Transit n",
                "Busiest block, KTV-VZM",
                "Busiest block, VZM-PSA",
                "Detention (h)",
                "Stabled loads",
                "Running excess (h)",
            ],
            [
                [
                    day,
                    r["loads_by_day"].get(day, 0),
                    t["by_day"].get(day, {}).get("median", ""),
                    f"{t['by_day'].get(day, {}).get('n', 0):g}",
                    max(
                        (
                            v.get(day, 0)
                            for k, v in ld["busiest_block_by_day"].items()
                            if k.startswith("ktv_vzm")
                        ),
                        default=0,
                    ),
                    max(
                        (
                            v.get(day, 0)
                            for k, v in ld["busiest_block_by_day"].items()
                            if k.startswith("vzm_psa")
                        ),
                        default=0,
                    ),
                    de["by_day"][day]["detention_hours"],
                    f"{de['by_day'][day]['stabled_loads']:g}",
                    de["by_day"][day]["running_excess_hours"],
                ]
                for day in sorted(de["by_day"])
            ],
        ),
        "",
        "Dwell per station and per day, loads per block and day, and the other fields are in the JSON file.",
        "",
        "## Definitions",
        "",
        *[f"- **{k}**: {v}" for k, v in r["definitions"].items()],
        "",
    ]
    return "\n".join(out)


def write(reference: dict[str, Any], out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / JSON_FILE, out_dir / MD_FILE]
    paths[0].write_text(to_json(reference), encoding="utf-8", newline="\n")
    paths[1].write_text(to_markdown(reference), encoding="utf-8", newline="\n")
    return paths


def diff(reference: dict[str, Any], out_dir: Path = OUT_DIR) -> list[str]:
    problems = []
    for name, text in ((JSON_FILE, to_json(reference)), (MD_FILE, to_markdown(reference))):
        path = out_dir / name
        if not path.exists():
            problems.append(f"{name}: missing")
        elif path.read_text(encoding="utf-8") != text:
            problems.append(f"{name}: differs")
    return problems


def run(mapping: MappingSpec | None = None, register: Register | None = None) -> dict[str, Any]:
    """Build the reference from the freight log (ETL stages 1, 2 and 6)."""
    from raildss_etl import freight, topology
    from raildss_etl.load import run_stage1
    from raildss_etl.mapping import load_mapping
    from raildss_params import load

    spec = mapping or load_mapping()
    reg = register or load()
    tables, _ = run_stage1(spec, write=False)
    topo, _ = topology.build_topology(tables, spec, reg)
    demand, stage6 = freight.build_freight(tables, topo, spec, reg)
    return build_reference(tables, topo, demand, stage6, spec, reg)
