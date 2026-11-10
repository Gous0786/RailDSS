"""DS-4 prediction: process times and ETA propagation (spec FR-PRED-03/04/05, REC-08; issue #72).

* **Process times** (FR-PRED-03, :class:`ProcessTimes`). Freight: P10 / P50 / P90 running time per block,
  direction and loaded/empty, and dwell per station and loaded/empty, from the real Aug 2025 freight log,
  trained on its first ``pred.train_days`` days. Below ``runtime.calibration.min_samples`` samples the
  table falls back to the technical minimum (block length over the calibrated freight cap) plus the
  calibrated block allowance. Passenger: the WTT's booked running and dwell times (a booked train never
  leaves before its booked time; there is no passenger running history to learn from).
* **Propagation** (FR-PRED-04, :class:`Predictor`). From the current state ``S_t`` (DS-3), each train's
  remaining path becomes a chain of events (DEP, ARR) linked by run and dwell activities; a plan adds
  ordering activities (``Ordering``: the second train may enter a block only after the first has left
  it). Earliest times are propagated twice, with P50 and with P90 process times (longest path over the
  event-activity graph), giving ETA/ETD P50 and P90 for every event in the horizon (``cd.horizon_min``).
* **Evaluation** (FR-PRED-05, :func:`evaluate`). On the held-out days of the log, every departure of a load
  predicts its arrivals downstream; errors are binned by lead time at ``pred.eval_horizons_min`` and
  compared with a persistence baseline (the speed of the last block persists) and a schedule baseline
  (technical minimum plus allowance, what a planner would book). Passenger ETAs cannot be evaluated: there
  is no real running record (no NTES, C-03).
"""

from __future__ import annotations

import datetime as dt
import json
import math
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from raildss_dss.state import PlanDirectory, StateEstimator, token
from raildss_etl.mapping import REPO
from raildss_params import Register, load

SECONDS_PER_MINUTE = 60
KMH_PER_MPS = 3.6
M_PER_KM = 1000
P50_COL, P90_COL = 2, 3  # columns of an activity (u, v, P50 s, P90 s)
P10, P50, P90 = 0.1, 0.5, 0.9  # the quantile levels of the tables
EVAL_DOC = REPO / "docs" / "dss" / "eta-evaluation.md"


# --------------------------------------------------------------------------- process times
@dataclass(frozen=True)
class Quantiles:
    """A process time in seconds. ``n`` samples (0 for a fallback or a booked time)."""

    p10: float
    p50: float
    p90: float
    n: int = 0
    source: str = "log"

    @classmethod
    def fixed(cls, seconds: float, source: str) -> Quantiles:
        return cls(seconds, seconds, seconds, 0, source)

    @classmethod
    def of(cls, samples: Sequence[float]) -> Quantiles:
        s = pd.Series(samples, dtype=float)
        return cls(float(s.quantile(P10)), float(s.quantile(P50)), float(s.quantile(P90)), len(samples))


@dataclass
class LogCall:
    station: str
    arr: dt.datetime
    dep: dt.datetime | None


@dataclass
class LogTrace:
    """One freight load's calls at section stations, in order (from the real log)."""

    load_id: str
    loaded: bool
    day: dt.date
    calls: list[LogCall]


def log_traces(tables: dict[str, pd.DataFrame], directory: PlanDirectory) -> list[LogTrace]:
    """Section calls of every load of the freight log (stage-1 tables)."""
    from raildss_etl.mapping import load_mapping
    from raildss_etl.topology import Codec

    codec = Codec(load_mapping())
    f = tables["FREIGHT"]
    f = f[f["Sttn"].isin(directory.km.keys()) & f["Arrival Time"].notna()]
    out = []
    for load_id, rows in f.groupby("LoadId", sort=False):
        rows = rows.sort_values("Arrival Time", kind="stable")
        calls: list[LogCall] = []
        for _, r in rows.iterrows():
            dep = r["Depart Time"].to_pydatetime() if pd.notna(r["Depart Time"]) else None
            if calls and calls[-1].station == r["Sttn"]:
                continue
            calls.append(LogCall(str(r["Sttn"]), r["Arrival Time"].to_pydatetime(), dep))
        if len(calls) > 1:
            loaded = bool(codec.decode("loaded_empty", rows.iloc[0]["LE"]))
            out.append(LogTrace(token(str(load_id)), loaded, calls[0].arr.date(), calls))
    return out


def split_days(traces: Sequence[LogTrace], train_days: int) -> tuple[list[dt.date], list[dt.date]]:
    days = sorted({t.day for t in traces})
    return days[:train_days], days[train_days:]


@dataclass
class ProcessTimes:
    """Process-time tables (FR-PRED-03) with the fallbacks."""

    directory: PlanDirectory
    caps_kmh: dict[str, float]  # "loaded" / "empty" (calibrated freight caps)
    passenger_cap_kmh: float
    allowance_s: dict[str, float]  # "A-B/DN" (calibrated block allowances)
    min_samples: int
    runs: dict[tuple[str, bool], Quantiles] = field(default_factory=dict)  # (block id, loaded)
    dwells: dict[tuple[str, bool], Quantiles] = field(default_factory=dict)  # (station, loaded)

    @classmethod
    def from_log(
        cls,
        traces: Iterable[LogTrace],
        directory: PlanDirectory,
        register: Register | None = None,
        calibration: dict[str, Any] | None = None,
    ) -> ProcessTimes:
        reg = register or load()
        cal = calibration or read_calibration()
        pt = cls(
            directory=directory,
            caps_kmh={k: float(v) for k, v in cal["freight_caps_kmh"].items()},
            passenger_cap_kmh=float(cal["passenger_cap_kmh"]),
            allowance_s={k: float(v) for k, v in cal["block_allowance_s"].items()},
            min_samples=int(reg.value("runtime.calibration")["min_samples"]),
        )
        runs: dict[tuple[str, bool], list[float]] = defaultdict(list)
        dwells: dict[tuple[str, bool], list[float]] = defaultdict(list)
        for tr in traces:
            for i, (a, b) in enumerate(zip(tr.calls, tr.calls[1:], strict=False)):
                block = directory.blocks.get((a.station, b.station))
                start = a.dep or a.arr
                if block is not None and b.arr >= start:
                    runs[(block, tr.loaded)].append((b.arr - start).total_seconds())
                if i > 0 and a.dep is not None and a.dep >= a.arr:
                    dwells[(a.station, tr.loaded)].append((a.dep - a.arr).total_seconds())
        pt.runs = {k: Quantiles.of(v) for k, v in runs.items() if len(v) >= pt.min_samples}
        pt.dwells = {k: Quantiles.of(v) for k, v in dwells.items() if len(v) >= pt.min_samples}
        return pt

    def direction(self, a: str, b: str) -> str:
        km = self.directory.km
        return "DN" if km[b] > km[a] else "UP"

    def technical_s(self, a: str, b: str, cap_kmh: float) -> float:
        """Technical minimum over the block at a constant cap plus the calibrated allowance."""
        block = self.directory.blocks.get((a, b), f"{a}-{b}")
        length = (
            self.directory.length_m.get(block) or abs(self.directory.km[b] - self.directory.km[a]) * M_PER_KM
        )
        return length / (cap_kmh / KMH_PER_MPS) + self.allowance_s.get(f"{a}-{b}/{self.direction(a, b)}", 0.0)

    def freight_run(self, a: str, b: str, loaded: bool) -> Quantiles:
        block = self.directory.blocks.get((a, b))
        hit = self.runs.get((block, loaded)) if block else None
        if hit is not None:
            return hit
        cap = self.caps_kmh["loaded" if loaded else "empty"]
        return Quantiles.fixed(self.technical_s(a, b, cap), "fallback")

    def freight_dwell(self, station: str, loaded: bool) -> Quantiles:
        return self.dwells.get((station, loaded)) or Quantiles.fixed(0.0, "fallback")

    def passenger_run(self, run_id: str, a: str, b: str) -> Quantiles:
        booked = self.directory.booked
        dep, arr = booked.get((run_id, a, "DEP")), booked.get((run_id, b, "ARR"))
        if dep is not None and arr is not None and arr >= dep:
            return Quantiles.fixed((arr - dep).total_seconds(), "wtt")
        return Quantiles.fixed(self.technical_s(a, b, self.passenger_cap_kmh), "fallback")

    def passenger_dwell(self, run_id: str, station: str) -> Quantiles:
        booked = self.directory.booked
        arr, dep = booked.get((run_id, station, "ARR")), booked.get((run_id, station, "DEP"))
        seconds = (dep - arr).total_seconds() if arr is not None and dep is not None and dep >= arr else 0.0
        return Quantiles.fixed(seconds, "wtt")


def read_calibration() -> dict[str, Any]:
    from raildss_etl.gradients import OUT_DIR

    doc = json.loads((OUT_DIR / "ktv_psa_runtime_calibration.json").read_text(encoding="utf-8"))
    return dict(doc["calibration"])


# --------------------------------------------------------------------------- propagation
@dataclass(frozen=True)
class Ordering:
    """Plan ordering: ``second`` may enter ``block`` (from its start station) only after ``first`` left it."""

    first: str
    second: str
    block: str


@dataclass(frozen=True)
class EventPrediction:
    run_id: str
    station: str
    kind: str  # ARR | DEP
    eta_p50: dt.datetime
    eta_p90: dt.datetime
    booked: dt.datetime | None
    stale: bool


@dataclass
class _Node:
    run_id: str
    station: str
    kind: str
    release: tuple[float, float]  # earliest time (P50, P90), seconds after t
    booked: dt.datetime | None
    stale: bool


class CycleError(ValueError):
    """The plan's orderings and the trains' paths form a cycle (no feasible propagation)."""


class Predictor:
    """FR-PRED-04: ETA/ETD P50 and P90 for every event of every train in the horizon, under a plan."""

    def __init__(self, process: ProcessTimes, register: Register | None = None) -> None:
        reg = register or load()
        self.process = process
        self.directory = process.directory
        self.horizon = dt.timedelta(minutes=float(reg.value("cd.horizon_min")))

    def _activity(self, run_id: str, a: str, b: str, kind: str) -> Quantiles:
        """Run (a -> b) or dwell (at a) of a train."""
        passenger = "@" in run_id
        loaded = self.directory.loaded.get(run_id, True)
        if kind == "run":
            return (
                self.process.passenger_run(run_id, a, b)
                if passenger
                else self.process.freight_run(a, b, loaded)
            )
        return self.process.passenger_dwell(run_id, a) if passenger else self.process.freight_dwell(a, loaded)

    def predict(
        self, estimator: StateEstimator, t: dt.datetime, orderings: Sequence[Ordering] = ()
    ) -> list[EventPrediction]:
        snap = estimator.snapshot(t)
        nodes: list[_Node] = []
        edges: list[tuple[int, int, float, float]] = []  # u, v, P50 s, P90 s
        index: dict[tuple[str, str, str], int] = {}

        def node(run_id: str, station: str, kind: str, release: tuple[float, float], stale: bool) -> int:
            booked = self.directory.booked.get((run_id, station, kind))
            if booked is not None and kind == "DEP" and "@" in run_id:  # never before the booked time
                floor = (booked - t).total_seconds()
                release = (max(release[0], floor), max(release[1], floor))
            index[(run_id, station, kind)] = len(nodes)
            nodes.append(_Node(run_id, station, kind, release, booked, stale))
            return len(nodes) - 1

        def since(when: dt.datetime | None) -> float:
            return (when - t).total_seconds() if when else 0.0

        for st in snap.trains:
            tr = estimator.trains[st.run_id]
            path = self.directory.path(st.run_id) or []
            if not path:
                continue
            if tr.station in path and tr.kind == "ARR":
                i = path.index(tr.station)
                if i == len(path) - 1:
                    continue
                dwell = self._activity(st.run_id, tr.station, "", "dwell")
                at = since(tr.station_time)
                start = node(
                    st.run_id,
                    tr.station,
                    "DEP",
                    (max(0.0, at + dwell.p50), max(0.0, at + dwell.p90)),
                    st.stale,
                )
            elif tr.station in path and tr.kind == "DEP":
                i = path.index(tr.station)
                if i == len(path) - 1:
                    continue
                run = self._activity(st.run_id, tr.station, path[i + 1], "run")
                i += 1
                at = since(tr.station_time)
                start = node(
                    st.run_id, path[i], "ARR", (max(0.0, at + run.p50), max(0.0, at + run.p90)), st.stale
                )
            else:  # inside a block, no station event yet: place by km
                block = self.directory.block_at(st.run_id, tr.km) if tr.km is not None else None
                pair = next(
                    (k for k, v in self.directory.blocks.items() if v == block and k[0] in path), None
                )
                if pair is None or pair[1] not in path:
                    continue
                a, b = pair
                run = self._activity(st.run_id, a, b, "run")
                km = self.directory.km
                left = abs(km[b] - (tr.km or km[a])) / (abs(km[b] - km[a]) or 1.0)
                i = path.index(b)
                start = node(st.run_id, b, "ARR", (run.p50 * left, run.p90 * left), st.stale)
            prev, here = start, path[i]
            kind = nodes[start].kind
            while True:
                if kind == "ARR":
                    if i == len(path) - 1:
                        break
                    q = self._activity(st.run_id, here, "", "dwell")
                    nxt = node(st.run_id, here, "DEP", (0.0, 0.0), st.stale)
                else:
                    q = self._activity(st.run_id, here, path[i + 1], "run")
                    i += 1
                    here = path[i]
                    nxt = node(st.run_id, here, "ARR", (0.0, 0.0), st.stale)
                edges.append((prev, nxt, q.p50, q.p90))
                prev, kind = nxt, nodes[nxt].kind
        for o in orderings:
            ends = next((k for k, v in self.directory.blocks.items() if v == o.block), None)
            if ends is None:
                continue
            left_block = index.get((token(o.first), ends[1], "ARR"))
            enter_block = index.get((token(o.second), ends[0], "DEP"))
            if left_block is not None and enter_block is not None:
                edges.append((left_block, enter_block, 0.0, 0.0))
        p50 = _longest(nodes, edges, P50_COL)
        p90 = _longest(nodes, edges, P90_COL)
        limit = self.horizon.total_seconds()
        return [
            EventPrediction(
                n.run_id,
                n.station,
                n.kind,
                t + dt.timedelta(seconds=p50[k]),
                t + dt.timedelta(seconds=p90[k]),
                n.booked,
                n.stale,
            )
            for k, n in enumerate(nodes)
            if p50[k] <= limit
        ]


def _longest(nodes: list[_Node], edges: list[tuple[int, int, float, float]], col: int) -> list[float]:
    """Earliest event times: longest path from the releases over the activity durations in column ``col``."""
    out_edges: dict[int, list[tuple[int, float]]] = defaultdict(list)
    indeg = [0] * len(nodes)
    for e in edges:
        out_edges[e[0]].append((e[1], float(e[col])))
        indeg[e[1]] += 1
    time = [n.release[0] if col == P50_COL else n.release[1] for n in nodes]
    ready = [k for k, d in enumerate(indeg) if d == 0]
    done = 0
    while ready:
        u = ready.pop()
        done += 1
        for v, dur in out_edges[u]:
            time[v] = max(time[v], time[u] + dur)
            indeg[v] -= 1
            if indeg[v] == 0:
                ready.append(v)
    if done != len(nodes):
        raise CycleError("the plan orderings and train paths form a cycle")
    return time


# --------------------------------------------------------------------------- evaluation (FR-PRED-05)
@dataclass
class HorizonScore:
    horizon_min: float
    n: int = 0
    errors: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))  # method -> minutes

    def mae(self, method: str) -> float | None:
        e = self.errors.get(method) or []
        return statistics.fmean(abs(x) for x in e) if e else None

    def rmse(self, method: str) -> float | None:
        e = self.errors.get(method) or []
        return math.sqrt(statistics.fmean(x * x for x in e)) if e else None


METHODS = ("model", "persistence", "schedule")


@dataclass
class EvalReport:
    train_days: list[dt.date]
    test_days: list[dt.date]
    tables_from_log: int
    loads_evaluated: int
    scores: list[HorizonScore]

    def render_markdown(self) -> str:
        train, test = self.train_days, self.test_days
        lines = [
            "# ETA accuracy on held-out real freight days (issue #72, FR-PRED-05)",
            "",
            "Generated by `python -m raildss_dss predict-eval --write`.",
            "",
            f"- Trained on {len(train)} days ({train[0]} to {train[-1]}); evaluated on {len(test)} "
            f"held-out days ({test[0]} to {test[-1]}), {self.loads_evaluated} loads.",
            f"- Process-time tables learnt from the log (n >= min samples): {self.tables_from_log}; "
            "the others fall back to the technical minimum plus the calibrated allowance.",
            "- Every departure of a held-out load predicts its arrivals downstream; an error is binned "
            "by its lead time (actual arrival minus departure) into the first horizon that covers it.",
            "- **model**: P50 running and dwell times from the training days. **persistence**: the "
            "speed of the load's last block persists. **schedule**: technical minimum plus allowance "
            "per block (what a planner would book; freight has no timetable).",
            "- Passenger ETAs are not evaluated: there is no real passenger running record (no NTES, C-03).",
            "",
            "| Horizon (min) | Predictions | MAE model | MAE persistence | MAE schedule "
            "| RMSE model | RMSE persistence | RMSE schedule |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]

        def f(x: float | None) -> str:
            return f"{x:.1f}" if x is not None else "-"

        for s in self.scores:
            lines.append(
                f"| {s.horizon_min:g} | {s.n} | "
                + " | ".join(f(s.mae(m)) for m in METHODS)
                + " | "
                + " | ".join(f(s.rmse(m)) for m in METHODS)
                + " |"
            )
        lines.append("")
        lines.append("Errors in minutes (predicted minus actual arrival).")
        return "\n".join(lines) + "\n"


def evaluate(
    traces: Sequence[LogTrace], directory: PlanDirectory, register: Register | None = None
) -> tuple[EvalReport, ProcessTimes]:
    """Train on the first ``pred.train_days`` days of the log and score ETAs on the rest."""
    reg = register or load()
    train_days, test_days = split_days(traces, int(reg.value("pred.train_days")))
    train = set(train_days)
    process = ProcessTimes.from_log([t for t in traces if t.day in train], directory, reg)
    horizons = sorted(float(h) for h in reg.value("pred.eval_horizons_min"))
    scores = [HorizonScore(h) for h in horizons]
    km = directory.km
    test = [t for t in traces if t.day not in train]
    for tr in test:
        cap = process.caps_kmh["loaded" if tr.loaded else "empty"]
        for i in range(1, len(tr.calls) - 1):
            origin, before = tr.calls[i], tr.calls[i - 1]
            tau = origin.dep or origin.arr
            last_s = (origin.arr - (before.dep or before.arr)).total_seconds()
            if last_s <= 0 or (before.station, origin.station) not in directory.blocks:
                continue
            speed = abs(km[origin.station] - km[before.station]) / last_s  # km per s, the last block
            model = sched = dist = 0.0
            for j in range(i + 1, len(tr.calls)):
                a, b = tr.calls[j - 1], tr.calls[j]
                if (a.station, b.station) not in directory.blocks:
                    break
                if j - 1 > i:
                    model += process.freight_dwell(a.station, tr.loaded).p50
                model += process.freight_run(a.station, b.station, tr.loaded).p50
                sched += process.technical_s(a.station, b.station, cap)
                dist += abs(km[b.station] - km[a.station])
                lead_min = (b.arr - tau).total_seconds() / SECONDS_PER_MINUTE
                if lead_min <= 0:
                    continue
                bin_ = next((s for s in scores if lead_min <= s.horizon_min), None)
                if bin_ is None:
                    break
                actual = (b.arr - tau).total_seconds()
                bin_.n += 1
                bin_.errors["model"].append((model - actual) / SECONDS_PER_MINUTE)
                bin_.errors["persistence"].append((dist / speed - actual) / SECONDS_PER_MINUTE)
                bin_.errors["schedule"].append((sched - actual) / SECONDS_PER_MINUTE)
    report = EvalReport(train_days, test_days, len(process.runs) + len(process.dwells), len(test), scores)
    return report, process


def run_evaluation(register: Register | None = None) -> EvalReport:
    """Load the log (stage 1) and the published network, then :func:`evaluate`."""
    from raildss_etl.load import run_stage1

    reg = register or load()
    directory = PlanDirectory.from_published(register=reg)
    tables = run_stage1(write=False)[0]
    report, _ = evaluate(log_traces(tables, directory), directory, reg)
    return report
