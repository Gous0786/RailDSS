"""Boundary and junction flows (issue #12; spec FR-TWIN-22, A-12, RK-21, R06 §7).

Every WTT train and every freight load gets an entry and an exit: a boundary id from
``config/boundaries.yaml`` (KTV-VSKP, KTV-KK, VZM-RGDA, NWP-GNPR, PSA-KUR) or
``local:<station>`` when it starts or ends inside the section.

WTT (per train version):

* exit: the last row's block, if it leaves the section, names the boundary; otherwise the train
  terminates at the last station (``local``);
* entry: ``local`` when TRAIN.SOURCE is the first station; else the reverse train's exit boundary
  at the same station (mirror); else the station's default boundary. A row's block is the block
  the train departs on, so the first row never shows where the train came from (trains such as
  KRDL-VSKP have a single row at KTV).

Freight (per load, division log): the neighbouring off-section station before the first / after
the last section row names the boundary (``via``); a load whose log starts / ends at a ``log_edge``
station crossed the division boundary; otherwise the load is ``local``. Unknown neighbours fall back
to the station's default boundary and are counted.

Cross-checks: a load whose log origin (destination) division is not the section's division must
not enter (exit) as ``local``; a WTT train that terminates locally must have that station as its
DESTINATION.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd
import yaml
from pydantic import BaseModel, Field, model_validator

from raildss_etl.freight import FreightDemand
from raildss_etl.mapping import REPO
from raildss_etl.timetable import Timetable
from raildss_etl.topology import Topology

CONFIG = REPO / "config" / "boundaries.yaml"
DEFAULT_OUT = REPO / "data" / "interim" / "ktv-psa" / "boundaries"
LOCAL = "local:"
SECTION_DIVISION = "WAT"
MINUTES_PER_HOUR = 60
END_OF_DAY = "24:00"


def _minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    value = int(h) * MINUTES_PER_HOUR + int(m)
    if not 0 <= value <= 24 * MINUTES_PER_HOUR or int(m) >= MINUTES_PER_HOUR:
        raise ValueError(f"bad time {hhmm!r}")
    return value


class Window(BaseModel):
    start: str
    end: str

    @model_validator(mode="after")
    def _ordered(self) -> Window:
        if _minutes(self.end) <= _minutes(self.start):
            raise ValueError(f"window {self.start}-{self.end}: end must be after start")
        return self

    def contains(self, minute_of_day: int) -> bool:
        return _minutes(self.start) <= minute_of_day < _minutes(self.end)


class Boundary(BaseModel):
    station: str
    towards: str = ""
    blocks: list[str]
    via: list[str] = Field(default_factory=list)
    log_edge: list[str] = Field(default_factory=list)
    default: bool = False


class BoundaryConfig(BaseModel):
    boundaries: dict[str, Boundary]
    acceptance_windows: dict[str, list[Window]] = Field(default_factory=dict)
    gap_fill: dict[str, dict[str, str]] = Field(default_factory=dict)  # entry/exit -> direction -> id

    @model_validator(mode="after")
    def _consistent(self) -> BoundaryConfig:
        problems: list[str] = []
        defaults = Counter(b.station for b in self.boundaries.values() if b.default)
        problems += [f"{st}: {n} default boundaries" for st, n in defaults.items() if n > 1]
        owner: dict[str, str] = {}
        for bid, b in self.boundaries.items():
            for blk in b.blocks:
                if blk in owner:
                    problems.append(f"block {blk} in {owner[blk]} and {bid}")
                owner[blk] = bid
        for bid, windows in self.acceptance_windows.items():
            if bid not in self.boundaries:
                problems.append(f"acceptance window for unknown boundary {bid}")
            ordered = sorted(windows, key=lambda w: _minutes(w.start))
            for w1, w2 in itertools.pairwise(ordered):
                if _minutes(w2.start) < _minutes(w1.end):
                    problems.append(f"{bid}: windows {w1.start}-{w1.end} and {w2.start}-{w2.end} overlap")
        problems += [
            f"gap_fill {end}/{d}: unknown boundary {bid}"
            for end, m in self.gap_fill.items()
            for d, bid in m.items()
            if bid not in self.boundaries
        ]
        if problems:
            raise ValueError("; ".join(problems))
        return self

    def is_open(self, boundary: str, when: dt.time) -> bool:
        windows = self.acceptance_windows.get(boundary, [])
        minute = when.hour * MINUTES_PER_HOUR + when.minute
        return not windows or any(w.contains(minute) for w in windows)

    def by_block(self) -> dict[str, str]:
        return {blk: bid for bid, b in self.boundaries.items() for blk in b.blocks}

    def at_station(self, station: str, neighbour: str | None) -> tuple[str | None, bool]:
        """Boundary for an off-section neighbour of ``station``; second value: default used."""
        candidates = {bid: b for bid, b in self.boundaries.items() if b.station == station}
        for bid, b in candidates.items():
            if neighbour in b.via:
                return bid, False
        for bid, b in candidates.items():
            if b.default:
                return bid, True
        return None, False


def load_config(path: Path = CONFIG) -> BoundaryConfig:
    return BoundaryConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def other_division(value: Any) -> bool:
    """True when a freight-log division field names a division other than the section's."""
    return str(value) not in (SECTION_DIVISION, "", "nan", "None")


class Flow(BaseModel):
    entry: str
    exit: str
    entry_station: str
    exit_station: str
    entry_rule: str
    exit_rule: str


class BoundaryReport(BaseModel):
    wtt_trains: int
    wtt_unassigned: list[str]
    wtt_entry_rules: dict[str, int]
    wtt_local_exit_destination_mismatch: list[str]
    freight_loads: int
    freight_unassigned: list[str]
    freight_entry_rules: dict[str, int]
    freight_exit_rules: dict[str, int]
    freight_default_neighbours: dict[str, int]
    freight_local_but_outside_od: dict[str, int]
    entries_per_day: dict[str, dict[str, dict[str, int]]]  # day -> "wtt"/"freight" -> boundary -> n
    exits_per_day: dict[str, dict[str, dict[str, int]]]
    freight_day_totals_match: bool

    @property
    def ok(self) -> bool:
        return (
            not self.wtt_unassigned
            and not self.freight_unassigned
            and not self.wtt_local_exit_destination_mismatch
            and not any(self.freight_local_but_outside_od.values())
            and self.freight_day_totals_match
        )


# ---------------------------------------------------------------- WTT


def assign_wtt(timetable: Timetable, trains: pd.DataFrame, cfg: BoundaryConfig) -> dict[str, Flow]:
    by_block = cfg.by_block()
    info = trains.set_index("TRAINID")
    ends: dict[str, tuple[Any, Any]] = {}
    for svc in timetable.services:
        pts = sorted(timetable.points_of(svc.train_id), key=lambda p: p.seq)
        ends[svc.train_id] = (pts[0], pts[-1])

    exits: dict[str, tuple[str, str]] = {}
    for tid, (_, last) in ends.items():
        bid = by_block.get(last.block_section_id or "")
        exits[tid] = (bid, "boundary block") if bid else (f"{LOCAL}{last.op_code}", "terminates")
    exit_by_train_no = {s.train_no: exits[s.train_id][0] for s in timetable.services}

    flows: dict[str, Flow] = {}
    for svc in timetable.services:
        first, last = ends[svc.train_id]
        source = str(info.loc[svc.train_id, "SOURCE"])
        mirror = exit_by_train_no.get(svc.reverse_train_no or "")
        if source == first.op_code:
            entry, rule = f"{LOCAL}{first.op_code}", "originates"
        elif mirror and not mirror.startswith(LOCAL) and cfg.boundaries[mirror].station == first.op_code:
            entry, rule = mirror, "reverse train"
        else:
            bid, _ = cfg.at_station(first.op_code, None)
            entry, rule = (bid, "station default") if bid else ("", "unassigned")
        flows[svc.train_id] = Flow(
            entry=entry,
            exit=exits[svc.train_id][0],
            entry_station=first.op_code,
            exit_station=last.op_code,
            entry_rule=rule,
            exit_rule=exits[svc.train_id][1],
        )
    return flows


# ---------------------------------------------------------------- freight


def assign_freight(
    demand: FreightDemand, log: pd.DataFrame, topology: Topology, cfg: BoundaryConfig
) -> tuple[dict[str, Flow], Counter[str]]:
    """Freight flows; the counter records neighbours resolved by a station default."""
    section = {o.code for o in topology.operational_points}
    ordered = log.sort_values(["LoadId", "Arrival Time"], kind="stable")
    stations = {str(k): [str(s) for s in g["Sttn"]] for k, g in ordered.groupby("LoadId", sort=False)}
    first = ordered.groupby("LoadId", sort=False).first()
    edges = {st: bid for bid, b in cfg.boundaries.items() for st in b.log_edge}
    via = {st: bid for bid, b in cfg.boundaries.items() for st in b.via}
    has_boundary = {b.station for b in cfg.boundaries.values()}
    defaults: Counter[str] = Counter()

    def side(end: str, station: str, neighbour: str | None, direction: str, outside: bool) -> tuple[str, str]:
        fill = cfg.gap_fill.get(end, {}).get(direction, "")
        if neighbour is None:
            if station in edges:
                return edges[station], "log edge"
            if not outside:
                return f"{LOCAL}{station}", "local"
            if station in has_boundary:
                return cfg.at_station(station, None)[0] or fill, "log gap at boundary station"
            return fill, "log gap (direction)"
        if station in has_boundary:
            bid, used_default = cfg.at_station(station, neighbour)
            if used_default:
                defaults[f"{station}<-{neighbour}"] += 1
                return bid or "", "station default"
            return bid or "", "via"
        if neighbour in via:
            return via[neighbour], "skipped station (via)"
        return fill, "skipped station (direction)"

    flows: dict[str, Flow] = {}
    for load in demand.loads:
        seq = stations[load.load_id]
        idx = [i for i, s in enumerate(seq) if s in section]
        before = seq[idx[0] - 1] if idx[0] > 0 else None
        after = seq[idx[-1] + 1] if idx[-1] + 1 < len(seq) else None
        row = first.loc[load.load_id]
        entry, entry_rule = side(
            "entry", seq[idx[0]], before, load.direction, other_division(row["From Division"])
        )
        exit_, exit_rule = side(
            "exit", seq[idx[-1]], after, load.direction, other_division(row["Division To"])
        )
        flows[load.load_id] = Flow(
            entry=entry,
            exit=exit_,
            entry_station=seq[idx[0]],
            exit_station=seq[idx[-1]],
            entry_rule=entry_rule,
            exit_rule=exit_rule,
        )
    return flows, defaults


# ---------------------------------------------------------------- report


def build_boundaries(
    timetable: Timetable,
    demand: FreightDemand,
    tables: dict[str, pd.DataFrame],
    topology: Topology,
    tz: dt.tzinfo,
    cfg: BoundaryConfig | None = None,
) -> tuple[dict[str, Flow], dict[str, Flow], BoundaryReport]:
    cfg = cfg or load_config()
    wtt = assign_wtt(timetable, tables["TRAIN"], cfg)
    freight, defaults = assign_freight(demand, tables["FREIGHT"], topology, cfg)

    trains = tables["TRAIN"].set_index("TRAINID")
    local_exit_mismatch = sorted(
        tid
        for tid, f in wtt.items()
        if f.exit.startswith(LOCAL) and str(trains.loc[tid, "DESTINATION"]) != f.exit_station
    )
    first_rows = (
        tables["FREIGHT"].sort_values(["LoadId", "Arrival Time"], kind="stable").groupby("LoadId").first()
    )
    outside = {"entry": 0, "exit": 0}
    for lid, f in freight.items():
        row = first_rows.loc[lid]
        if f.entry.startswith(LOCAL) and str(row["From Division"]) not in (
            SECTION_DIVISION,
            "",
            "nan",
            "None",
        ):
            outside["entry"] += 1
        if f.exit.startswith(LOCAL) and str(row["Division To"]) not in (SECTION_DIVISION, "", "nan", "None"):
            outside["exit"] += 1

    entries: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    exits: dict[str, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(dict))
    totals_match = True
    for day in demand.days:
        key = day.day.isoformat()
        fe = Counter(freight[i].entry for i in day.run_ids)
        fx = Counter(freight[i].exit for i in day.run_ids)
        totals_match &= sum(fe.values()) == len(day.run_ids)
        runs = timetable.runs_on(day.day, tz, topology.network_version)
        we = Counter(wtt[r.train_id].entry for r in runs if r.train_id)
        wx = Counter(wtt[r.train_id].exit for r in runs if r.train_id)
        entries[key]["freight"], exits[key]["freight"] = dict(sorted(fe.items())), dict(sorted(fx.items()))
        entries[key]["wtt"], exits[key]["wtt"] = dict(sorted(we.items())), dict(sorted(wx.items()))

    report = BoundaryReport(
        wtt_trains=len(wtt),
        wtt_unassigned=sorted(t for t, f in wtt.items() if not f.entry or not f.exit),
        wtt_entry_rules=dict(Counter(f.entry_rule for f in wtt.values())),
        wtt_local_exit_destination_mismatch=local_exit_mismatch,
        freight_loads=len(freight),
        freight_unassigned=sorted(lid for lid, f in freight.items() if not f.entry or not f.exit),
        freight_entry_rules=dict(Counter(f.entry_rule for f in freight.values()).most_common()),
        freight_exit_rules=dict(Counter(f.exit_rule for f in freight.values()).most_common()),
        freight_default_neighbours=dict(defaults.most_common()),
        freight_local_but_outside_od=outside,
        entries_per_day={d: dict(v) for d, v in entries.items()},
        exits_per_day={d: dict(v) for d, v in exits.items()},
        freight_day_totals_match=totals_match,
    )
    return wtt, freight, report


def write_boundaries(
    wtt: dict[str, Flow], freight: dict[str, Flow], report: BoundaryReport, out_dir: Path = DEFAULT_OUT
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, flows, key in (("wtt_flows.csv", wtt, "train_id"), ("freight_flows.csv", freight, "load_id")):
        rows = [{key: k, **f.model_dump()} for k, f in sorted(flows.items())]
        pd.DataFrame(rows).to_csv(out_dir / name, index=False, lineterminator="\n")
    (out_dir / "report.json").write_text(json.dumps(report.model_dump(), indent=1) + "\n", encoding="utf-8")
