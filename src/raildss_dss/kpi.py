"""KPI engine: the MVP KPI set, plan deltas and the DSS-vs-baseline comparison (spec §10; FR-KPI-01..05,
FR-KPI-08; issue #81).

:func:`compute` reads an event stream, ground truth (``gt.*``, evaluation) or the observed feeds
(``obs.*``, live), with the same record types: ``TDS_OCCUPANCY`` (entry and exit), ``DELAY_RECORD``
(arrival and departure delays at stations) and ``TRAIN_STATE`` (position and status). It also takes a
per-train table (:class:`TrainInfo`: kind, precedence class, booked entry and exit, and for freight its
unimpeded running time). From those it returns a :class:`KpiReport` with:

* **A2** section punctuality (entry on time and exit on time, over entry on time) and LOP;
* **A4** mean and maximum arrival delay;
* **A5** weighted delay ``sum(w_class * (exit delay - entry delay)+)``, with the weights from the policy
  table the optimiser objective uses (:mod:`raildss_params.policy`, FR-KPI-03). Freight exit delay is
  the transit time beyond its unimpeded running time. A short-terminated or cancelled train adds its class
  weight times ``opt.disruption.terminate_penalty_min`` (issue #153);
* **A6** mean, median and P90 exit delay;
* **A9** time lost in section;
* **B1** trains that left the section, by direction and kind;
* **C1** freight average speed;
* **C2** freight detention hours (time standing);
* **F1** unplanned stops (times a train started waiting).

:func:`delta` gives a plan's KPIs against a reference run (do nothing, or BL-0), including the three most
affected trains (FR-KPI-02). :func:`compare_runs` lays out the DSS-vs-baseline comparison of a scenario or
a day (FR-KPI-05). :func:`snapshot_rows` and :func:`store` persist the report in the ``kpi_snapshot``
hypertable (FR-KPI-08).
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from pydantic import BaseModel, Field

from raildss_common.envelope import EventEnvelope, EventType
from raildss_params import Register, load, policy

SECONDS_PER_MIN = 60
SECONDS_PER_HOUR = 3600
P90 = 0.9
STANDING = ("waiting", "dwelling")


class TrainInfo(BaseModel):
    run_id: str  # as in the event stream (``train_run_id``)
    kind: str  # passenger | freight
    category: str  # precedence class of the policy table (policy.weights)
    direction: str = ""
    booked_entry_s: float | None = None
    booked_exit_s: float | None = None
    free_run_s: float | None = None  # freight: unimpeded running time through the section


class TrainKpi(BaseModel):
    run_id: str
    kind: str
    category: str
    weight: float
    entry_s: float | None = None
    exit_s: float | None = None
    entry_delay_min: float | None = None
    exit_delay_min: float | None = None
    weighted_delay_min: float = 0.0  # this train's A5 term
    standing_min: float = 0.0
    unplanned_stops: int = 0
    km: float = 0.0


class KpiReport(BaseModel):
    scope: str  # e.g. "day 2025-08-20" or "SCN-04"
    arm: str  # e.g. "BL-0", "do-nothing", "DSS"
    policy_version: str
    network_version: str
    kpis: dict[str, float]
    by_direction: dict[str, int] = Field(default_factory=dict)
    trains: list[TrainKpi] = Field(default_factory=list)


def _p90(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(P90 * len(ordered)))]


def compute(
    events: Iterable[EventEnvelope],
    trains: Mapping[str, TrainInfo],
    origin: dt.datetime,
    *,
    scope: str,
    arm: str,
    network_version: str,
    register: Register | None = None,
    tolerance_min: float | None = None,
) -> KpiReport:
    """The MVP KPI set of one run (FR-KPI-01)."""
    reg = register or load()
    tol = tolerance_min if tolerance_min is not None else float(reg.value("kpi.punctuality_tolerance_min"))
    rows = {
        rid: TrainKpi(run_id=rid, kind=t.kind, category=t.category, weight=policy.weight_of(t.category, reg))
        for rid, t in trains.items()
    }
    arrivals: dict[str, list[float]] = {}
    last_state: dict[str, tuple[float, str, float | None]] = {}
    km_seen: dict[str, list[float]] = {}
    ended: set[str] = set()  # short-terminated or cancelled (issue #156): they did not leave the section
    for ev in sorted(events, key=lambda e: (e.event_time, e.source, e.seq)):
        rid = ev.train_run_id
        if rid is None or rid not in rows:
            continue
        r = rows[rid]
        t = (ev.event_time - origin).total_seconds()
        p = ev.payload
        if ev.event_type is EventType.TDS_OCCUPANCY:
            if p.get("occupied") and r.entry_s is None:
                r.entry_s = t
            elif not p.get("occupied"):
                r.exit_s = t  # the last release is the exit
        elif ev.event_type is EventType.DELAY_RECORD and p.get("kind") == "arrival":
            arrivals.setdefault(rid, []).append(float(p["delay_s"]) / SECONDS_PER_MIN)
        elif ev.event_type is EventType.TRAIN_STATE:
            status = str(p.get("status", ""))
            if status == "terminated":
                ended.add(rid)
            km = ev.location.km if ev.location is not None else None
            if km is not None:
                km_seen.setdefault(rid, []).append(km)
            prev = last_state.get(rid)
            if prev is not None and prev[1] in STANDING:
                r.standing_min += (t - prev[0]) / SECONDS_PER_MIN
            if status == "waiting" and (prev is None or prev[1] != "waiting"):
                r.unplanned_stops += 1
            last_state[rid] = (t, status, km)
    for rid in ended:
        rows[rid].exit_s = None
    for rid, r in rows.items():
        info = trains[rid]
        if km_seen.get(rid):
            r.km = abs(km_seen[rid][-1] - km_seen[rid][0])
        arr = arrivals.get(rid, [])
        if arr:
            r.entry_delay_min, r.exit_delay_min = arr[0], arr[-1]
        elif info.free_run_s is not None and r.entry_s is not None and r.exit_s is not None:
            r.entry_delay_min = 0.0
            r.exit_delay_min = max(0.0, (r.exit_s - r.entry_s - info.free_run_s) / SECONDS_PER_MIN)
        if r.entry_delay_min is not None and r.exit_delay_min is not None:
            r.weighted_delay_min = r.weight * max(0.0, r.exit_delay_min - r.entry_delay_min)
    # a short-terminated or cancelled train counts at the penalty the planner ranks it with (issue #153):
    # its delay up to where it ended plus opt.disruption.terminate_penalty_min, weighted by its class
    penalty = float(reg.value("opt.disruption")["terminate_penalty_min"])
    for rid in ended:
        rows[rid].weighted_delay_min += rows[rid].weight * penalty
    done = [r for r in rows.values() if r.exit_s is not None and r.entry_s is not None]
    pax = [r for r in done if r.kind == "passenger" and r.exit_delay_min is not None]
    frt = [r for r in done if r.kind == "freight"]
    on_time_in = [r for r in pax if (r.entry_delay_min or 0.0) <= tol]
    on_time_both = [r for r in on_time_in if (r.exit_delay_min or 0.0) <= tol]
    exit_delays = [r.exit_delay_min or 0.0 for r in pax]
    all_arr = [d for rid in rows for d in arrivals.get(rid, [])]
    lost = [
        (
            (r.exit_s or 0)
            - (r.entry_s or 0)
            - ((trains[r.run_id].booked_exit_s or 0) - (trains[r.run_id].booked_entry_s or 0))
        )
        / SECONDS_PER_MIN
        for r in pax
        if trains[r.run_id].booked_entry_s is not None and trains[r.run_id].booked_exit_s is not None
    ]
    frt_hours = sum(((r.exit_s or 0) - (r.entry_s or 0)) / SECONDS_PER_HOUR for r in frt)
    by_dir: dict[str, int] = {}
    for r in done:
        key = f"{trains[r.run_id].direction or '-'}/{r.kind}"
        by_dir[key] = by_dir.get(key, 0) + 1
    kpis = {
        "A2_section_punctuality_pct": round(100 * len(on_time_both) / len(on_time_in), 2)
        if on_time_in
        else 0.0,
        "A2_lop_count": float(len(on_time_in) - len(on_time_both)),
        "A4_mean_arrival_delay_min": round(statistics.fmean(all_arr), 2) if all_arr else 0.0,
        "A4_max_arrival_delay_min": round(max(all_arr), 2) if all_arr else 0.0,
        "A5_weighted_delay_train_min": round(sum(r.weighted_delay_min for r in rows.values()), 2),
        "A6_mean_exit_delay_min": round(statistics.fmean(exit_delays), 2) if exit_delays else 0.0,
        "A6_median_exit_delay_min": round(statistics.median(exit_delays), 2) if exit_delays else 0.0,
        "A6_p90_exit_delay_min": round(_p90(exit_delays), 2),
        "A9_mean_time_lost_min": round(statistics.fmean(lost), 2) if lost else 0.0,
        "B1_trains_exited": float(len(done)),
        "C1_freight_speed_kmh": round(sum(r.km for r in frt) / frt_hours, 2) if frt_hours else 0.0,
        "C2_freight_detention_h": round(sum(r.standing_min for r in frt) / SECONDS_PER_MIN, 2),
        "F1_unplanned_stops": float(sum(r.unplanned_stops for r in rows.values())),
    }
    return KpiReport(
        scope=scope,
        arm=arm,
        policy_version=reg.version,
        network_version=network_version,
        kpis=kpis,
        by_direction=dict(sorted(by_dir.items())),
        trains=sorted(rows.values(), key=lambda r: r.run_id),
    )


def delta(plan: KpiReport, reference: KpiReport, top: int = 3) -> dict[str, Any]:
    """FR-KPI-02: KPI differences (plan - reference) and the most affected trains."""
    ref = {t.run_id: t for t in reference.trains}
    changes = []
    for t in plan.trains:
        r = ref.get(t.run_id)
        if r is not None and t.exit_delay_min is not None and r.exit_delay_min is not None:
            changes.append((round(t.exit_delay_min - r.exit_delay_min, 2), t.run_id))
    changes.sort(key=lambda c: (-abs(c[0]), c[1]))
    return {
        "against": reference.arm,
        "kpis": {k: round(plan.kpis[k] - reference.kpis.get(k, 0.0), 2) for k in plan.kpis},
        "most_affected": [{"run_id": rid, "delta_exit_delay_min": d} for d, rid in changes[:top] if d],
    }


def compare_runs(dss: KpiReport, baselines: Sequence[KpiReport]) -> dict[str, Any]:
    """FR-KPI-05: the DSS-vs-baseline comparison of one scope (a scenario or a day)."""
    return {
        "scope": dss.scope,
        "arms": {r.arm: r.kpis for r in (dss, *baselines)},
        "deltas": {b.arm: delta(dss, b) for b in baselines},
    }


def comparison_markdown(comp: Mapping[str, Any]) -> str:
    arms = list(comp["arms"])
    keys = list(comp["arms"][arms[0]])
    lines = [f"## {comp['scope']}", "", "| KPI | " + " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    lines += [f"| {k} | " + " | ".join(f"{comp['arms'][a][k]:g}" for a in arms) + " |" for k in keys]
    return "\n".join(lines) + "\n"


def snapshot_rows(report: KpiReport, at: dt.datetime) -> list[dict[str, Any]]:
    """Rows of the ``kpi_snapshot`` hypertable (FR-KPI-08)."""
    return [
        {
            "time": at,
            "scope": report.scope,
            "arm": report.arm,
            "kpi": k,
            "value": v,
            "policy_version": report.policy_version,
            "network_version": report.network_version,
        }
        for k, v in sorted(report.kpis.items())
    ]


def store(conn: Any, report: KpiReport, at: dt.datetime) -> int:
    """Insert a report into ``kpi_snapshot`` (a pg8000 native connection, as in ``raildss_schema``)."""
    rows = snapshot_rows(report, at)
    for row in rows:
        conn.run(
            "INSERT INTO kpi_snapshot (time, scope, arm, kpi, value, policy_version, network_version) "
            "VALUES (:time, :scope, :arm, :kpi, :value, :policy_version, :network_version)",
            **row,
        )
    return len(rows)
