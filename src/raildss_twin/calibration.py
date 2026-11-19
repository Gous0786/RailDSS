"""Running-time calibration against the freight log and the WTT (issue #16; spec DSC-10, REC-08).

Two layers, both reported:

1. **Physics.** The point-mass model (:mod:`raildss_twin.running_time`) with one cruise cap per
   class (freight loaded, freight empty, passenger) chosen from ``runtime.calibration`` on the
   physics-only fit.
2. **Supplements.** Location effects the model has no data for (junction and yard approaches,
   signal sighting) are absorbed by supplements:

   * freight: one allowance per block and direction, shared by loaded and empty trains (one value
     against two references), = max(0, mean residual);
   * passenger: one signed supplement per WTT leg, direction and stopping context (pass or stop at
     each end), fitted jointly on every train's stop-to-stop segments with a robust (absolute-error)
     loss, each leg bounded below by its physics at line speed (:func:`fit_supplements`, issue #137).
     Never per span or per train.

   A stop-to-stop passenger run leaves from and is received on the station line of its booked
   platform (WTT planned line -> ``platform`` -> ``station_line``) at ``station_line.speed_kmh``
   (issue #59, :meth:`Network.approach_restrictions`).

References:

* freight: the log p10 per adjacent block, direction and loaded/empty (``n >= min_samples``),
  simulated pass to pass; target +-``freight_tolerance``. The model's reference for a block is the
  load-weighted 10th percentile of its wagon families' physics times (the block's wagon mix in the
  log, each family on its own consist template, issue #60), as the log p10 is taken over those loads;
* passenger: WTT stop-to-stop spans of >= ``span_min_legs`` legs. The reference of each span is its
  own train's booked time net of the WTT's engineering and traffic allowances (DR-0005, issue #137).
  A span is simulated as the sum of its legs, each in its stopping context (acceleration and braking
  from physics), plus their supplements. Target +-``span_tolerance_s``.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np
import pandas as pd
from pydantic import BaseModel

from raildss_etl.freight import FreightDemand
from raildss_etl.gradients import OUT_DIR, read_gradients
from raildss_etl.mapping import REPO
from raildss_etl.speeds import Speeds, Stage4Report
from raildss_etl.timetable import Timetable
from raildss_etl.topology import Topology
from raildss_params import Register, load
from raildss_schema.entities import (
    BlockSection,
    BlockSectionLine,
    Gradient,
    OperationalPoint,
    Platform,
    SpeedProfile,
    StationLine,
)
from raildss_twin.rolling_stock import ConsistTemplate, RollingStock
from raildss_twin.running_time import M_PER_KM, Driving, Restriction, Track, simulate

CALIBRATION_FILE = OUT_DIR / "ktv_psa_runtime_calibration.json"
REFERENCE_FILE = OUT_DIR / "ktv_psa_runtime_reference_seconds.json"
REFERENCE_TOLERANCE_S = 1  # a recorded run may differ by rounding only (DSC-10: reproducible to 1 s)
WORST = 10
REFERENCE_QUANTILE = 0.1  # the log reference is a p10: the 10th percentile over loads


class Residual(BaseModel):
    ref: str
    direction: str
    kind: str
    reference_s: float
    physics_s: int
    supplement_s: float
    simulated_s: float
    residual: float  # fraction (freight) or seconds (passenger)
    within: bool
    wagon_mix: dict[str, int] = {}  # freight: loads per wagon family on the block (issue #60)
    approach: str = ""  # passenger: booked departure / reception station lines (issue #59)


class CalibrationReport(BaseModel):
    freight_caps_kmh: dict[str, float]
    passenger_cap_kmh: float
    freight_blocks: int
    freight_physics_within: int
    freight_within: int
    freight_share: float
    spans_total: int
    spans_scored: int
    spans_physics_within: int
    spans_no_approach_within: int = 0  # physics + supplements, no station-line approach (issue #59)
    spans_within: int
    spans_share: float
    pass_slower_than_stop: list[str]
    reference_runs: int  # recorded expected-seconds runs the physics is checked against
    reference_mismatches: list[str]  # runs whose seconds moved; empty when the physics is unchanged
    reproducible: bool  # recorded runs exist and all reproduce within REFERENCE_TOLERANCE_S
    worst_blocks: list[Residual]
    freight_rows: list[Residual] = []  # every scored freight block, with its wagon mix (issue #60)
    worst_spans: list[Residual]
    span_rows: list[Residual] = []  # every scored passenger span (issue #59)

    freight_target_met: bool
    passenger_target_met: bool  # DSC-10 passenger share; a shortfall is documented, not fitted away

    @property
    def ok(self) -> bool:
        """Freight share, pass <= stop-start and reproducibility (the passenger share is reported)."""
        return self.freight_target_met and not self.pass_slower_than_stop and self.reproducible


class Span(NamedTuple):
    train_no: str
    coaches: int
    stops: tuple[str, ...]  # stations from the first stop to the next stop
    net_s: int  # the train's own booked time, net of engineering and traffic allowances (reference)
    dep_platform: str | None = None  # booked platform at the first stop (WTT planned line)
    arr_platform: str | None = None  # booked platform at the last stop
    legs: tuple[Leg, ...] = ()  # the span's legs, first from a stop, last to a stop


class Approach(NamedTuple):
    """The station line a train leaves from or is received on (issue #59).

    Reception: the train runs over the turnout and the line at ``speed_kmh`` for the line's clear
    standing room before it stops (it stops at the far end of the line). Departure: it is held to
    ``speed_kmh`` until its rear clears the exit turnout (one train length).
    """

    op: str
    line_no: int
    kind: str  # main | loop | siding | yard
    speed_kmh: float
    csr_m: float

    def label(self) -> str:
        return f"{self.op}/{self.line_no} {self.kind} {self.speed_kmh:g}"


class Calibration(BaseModel):
    freight_caps_kmh: dict[str, float]  # "loaded" / "empty"
    passenger_cap_kmh: float
    block_allowance_s: dict[str, float]  # "A-B/DN"
    leg_supplement_s: dict[str, float]  # "A-B/pass-stop": leg, direction and stopping context (#137)


# ---------------------------------------------------------------- track


class Network:
    """Per-direction tracks for passenger and goods speed limits, plus station chainage."""

    def __init__(
        self,
        topology: Topology,
        speeds: Speeds,
        speeds_report: Stage4Report,
        gradients: list[Gradient],
        platforms: Sequence[Platform] = (),
    ) -> None:
        psr = [
            (p.id, p.direction, p.from_km_hwh, p.to_km_hwh, frozenset(p.lines))
            for p in speeds_report.placements
        ]
        self._build(
            topology.operational_points, topology.block_sections, speeds.speed_profiles, psr, gradients
        )
        self._stations(speeds.station_lines, platforms)

    @classmethod
    def from_entities(
        cls,
        operational_points: Sequence[OperationalPoint],
        block_sections: Sequence[BlockSection],
        speed_profiles: Sequence[SpeedProfile],
        gradients: Sequence[Gradient],
        block_section_lines: Sequence[BlockSectionLine] = (),
        station_lines: Sequence[StationLine] = (),
        platforms: Sequence[Platform] = (),
    ) -> Network:
        """Build from published entities. A PSR profile's locations carry the HWH chainage.

        A PSR applies to the lines its block has in the PSR's direction (as the ETL places it); a
        profile with no such line on record keeps its ``line_id``, else applies to every line.
        """
        on_block: dict[tuple[str, str], set[str]] = defaultdict(set)
        for ln in block_section_lines:
            for d in ("DN", "UP") if ln.direction == "BOTH" else (ln.direction,):
                on_block[(ln.block_section_id, d)].add(ln.line_id)
        psr = []
        for p in speed_profiles:
            if p.kind != "PSR":
                continue
            direction = p.direction or "DN"
            lines = on_block.get((p.block_section_id or "", direction)) or (
                {p.line_id} if p.line_id else None
            )
            psr.append(
                (
                    p.id,
                    direction,
                    p.location_from.km or 0.0,
                    p.location_to.km or 0.0,
                    frozenset(lines) if lines else None,
                )
            )
        net = cls.__new__(cls)
        net._build(operational_points, block_sections, speed_profiles, psr, gradients)
        net._stations(station_lines, platforms)
        return net

    def _stations(self, station_lines: Sequence[StationLine], platforms: Sequence[Platform]) -> None:
        """Station lines with a speed and a CSR, and the line behind each platform (issue #59)."""
        self.station_lines: dict[tuple[str, int], StationLine] = {
            (sl.op_code, sl.line_no): sl for sl in station_lines if sl.line_speed_kmh and sl.csr_m
        }
        self.platform_line: dict[tuple[str, str], int] = {
            (p.op_code, p.platform_no): p.station_line_no for p in platforms if p.station_line_no is not None
        }

    def approach(self, op: str, platform: str | None = None, line_no: int | None = None) -> Approach | None:
        """The station line behind a booked platform (or a line number); None if unknown."""
        if line_no is None and platform is not None:
            line_no = self.platform_line.get((op, platform))
        sl = self.station_lines.get((op, line_no)) if line_no is not None else None
        if sl is None or sl.line_speed_kmh is None or sl.csr_m is None:
            return None
        return Approach(op, sl.line_no, sl.kind, sl.line_speed_kmh, sl.csr_m)

    def approach_restrictions(
        self, a: str, b: str, dep: Approach | None, arr: Approach | None
    ) -> list[Restriction]:
        """Station-line speed limits for a run from a stop at ``a`` to a stop at ``b``.

        Departure: a zero-length limit at ``a``; the running-time model holds it until the rear of the
        train has cleared it, i.e. for one train length (the exit turnout is at the starter). Reception:
        the line's CSR before ``b`` (the train stops at the far end of the line).
        """
        out = []
        if dep is not None:
            out.append(Restriction(self.km[a], self.km[a], dep.speed_kmh))
        if arr is not None:
            kb, length = self.km[b], arr.csr_m / M_PER_KM
            lo, hi = (kb - length, kb) if self.km[b] > self.km[a] else (kb, kb + length)
            out.append(Restriction(lo, hi, arr.speed_kmh))
        return out

    def _build(
        self,
        operational_points: Sequence[OperationalPoint],
        block_sections: Sequence[BlockSection],
        speed_profiles: Sequence[SpeedProfile],
        psr_places: Sequence[tuple[str, str, float, float, frozenset[str] | None]],
        gradients: Sequence[Gradient],
    ) -> None:
        ops = sorted(operational_points, key=lambda o: o.km)
        self.km = {o.code: o.km for o in ops}
        hwh = np.array([o.km_ref_hwh for o in ops], dtype=float)
        sec = np.array([o.km for o in ops])
        coa = [b for b in block_sections if b.level == "coa"]
        dn = {b.id for b in coa if b.direction == "DN"}
        grads = sorted((g for g in gradients if g.block_section_id in dn), key=lambda g: g.from_km)
        profiles = {s.id: s for s in speed_profiles}
        self.tracks: dict[tuple[str, str], Track] = {}
        for direction in ("DN", "UP"):
            line = [
                Restriction(
                    min(self.km[b.from_op], self.km[b.to_op]),
                    max(self.km[b.from_op], self.km[b.to_op]),
                    b.vmax_kmh,
                )
                for b in coa
                if b.direction == direction
            ]
            for kind in ("passenger", "goods"):
                psr = []
                for pid, pdir, from_hwh, to_hwh, lines in psr_places:
                    if pdir != direction:
                        continue
                    a, b = sorted(float(np.interp(-h, -hwh, sec)) for h in (from_hwh, to_hwh))
                    prof = profiles[pid]
                    psr.append(
                        Restriction(
                            a,
                            b,
                            prof.vmax_passenger_kmh if kind == "passenger" else prof.vmax_goods_kmh,
                            lines,
                        )
                    )
                self.tracks[(direction, kind)] = Track(
                    increasing=direction == "DN",
                    line_speed=line,
                    restrictions=psr,
                    grade_breaks=[g.from_km for g in grads],
                    grade_permille=[g.gradient_permille for g in grads],
                    line=direction,  # the main line of the direction
                )

    def direction(self, a: str, b: str) -> str:
        return "DN" if self.km[b] > self.km[a] else "UP"


class Runner:
    """Memoised simulations for one register."""

    def __init__(self, network: Network, register: Register) -> None:
        self.net = network
        model = register.value("runtime.model")
        self.step = float(model["step_m"])
        self.rot = {k: float(v) for k, v in model["rotational_mass_factor"].items()}
        self._cache: dict[tuple[Any, ...], int] = {}

    def time(
        self,
        template: ConsistTemplate,
        a: str,
        b: str,
        start_stopped: bool,
        end_stopped: bool,
        cap: float,
        dep: Approach | None = None,
        arr: Approach | None = None,
    ) -> int:
        """Running time from ``a`` to ``b``. ``dep`` / ``arr``: the station line the train starts from
        (when ``start_stopped``) or is received on (when ``end_stopped``), issue #59."""
        dep = dep if start_stopped else None
        arr = arr if end_stopped else None
        key = (template.consist.id, a, b, start_stopped, end_stopped, cap, dep, arr)
        if key not in self._cache:
            passenger = template.consist_type == "passenger"
            track = self.net.tracks[(self.net.direction(a, b), "passenger" if passenger else "goods")]
            extra = self.net.approach_restrictions(a, b, dep, arr)
            if extra:
                track = dataclasses.replace(track, restrictions=[*track.restrictions, *extra])
            driving = Driving(
                cruise_kmh=cap, rotational_mass_factor=self.rot["passenger" if passenger else "freight"]
            )
            run = simulate(
                track,
                template,
                self.net.km[a],
                self.net.km[b],
                start_stopped,
                end_stopped,
                driving,
                self.step,
            )
            self._cache[key] = run.time_s
        return self._cache[key]


# ---------------------------------------------------------------- freight


def wagon_mix(
    demand: FreightDemand, rs: RollingStock, blocks: set[tuple[str, bool]]
) -> tuple[dict[tuple[str, bool], Counter[str]], dict[str, str]]:
    """Loads per wagon family on each (block, loaded) of ``blocks``, and one wagon code per family."""
    mix: dict[tuple[str, bool], Counter[str]] = defaultdict(Counter)
    code_of: dict[str, str] = {}
    for ld in demand.loads:
        family = rs.family_of(ld.wagon_type)
        code_of.setdefault(family, ld.wagon_type.strip())
        for block in set(ld.path):
            if (block, ld.loaded) in blocks:
                mix[(block, ld.loaded)][family] += 1
    return dict(mix), code_of


def weighted_quantile(values: Sequence[tuple[int, int]], q: float) -> int:
    """Lower ``q`` quantile of ``(value, weight)`` pairs: the smallest value whose cumulative weight
    reaches ``q`` of the total."""
    ordered = sorted(values)
    total = sum(w for _, w in ordered)
    seen = 0
    for value, weight in ordered:
        seen += weight
        if seen >= q * total:
            return value
    return ordered[-1][0]


def _freight(
    runner: Runner, demand: FreightDemand, rs: RollingStock, cfg: dict[str, Any]
) -> tuple[dict[str, float], dict[str, float], list[Residual], int]:
    targets = [b for b in demand.block_times if b.n >= int(cfg["min_samples"])]
    mix, code_of = wagon_mix(demand, rs, {(b.block, b.loaded) for b in targets})
    reference = str(cfg["freight_reference_wagon"])
    tol = float(cfg["freight_tolerance"])

    def physics(caps: tuple[float, float]) -> list[int]:
        out = []
        for b in targets:
            a, c = b.block.split("-")
            cap = caps[0] if b.loaded else caps[1]
            families = mix.get((b.block, b.loaded)) or Counter({rs.family_of(reference): 1})
            times = [
                (runner.time(rs.freight(code_of.get(f, reference), b.loaded), a, c, False, False, cap), n)
                for f, n in families.items()
            ]
            out.append(weighted_quantile(times, REFERENCE_QUANTILE))
        return out

    def within(sim: float, ref: float) -> bool:
        return abs(sim - ref) <= tol * ref

    grid = [float(c) for c in cfg["freight_caps_kmh"]]
    best = max(
        ((cl, ce) for cl in grid for ce in grid),
        key=lambda caps: (
            sum(within(s, b.p10_min * 60) for s, b in zip(physics(caps), targets, strict=True)),
            caps,
        ),
    )
    phys = physics(best)
    resid: dict[str, list[float]] = defaultdict(list)
    for s, b in zip(phys, targets, strict=True):
        resid[f"{b.block}/{b.direction}"].append(b.p10_min * 60 - s)
    allowance = {k: round(max(0.0, statistics.mean(v)), 1) for k, v in resid.items()}
    rows = []
    for s, b in zip(phys, targets, strict=True):
        ref = b.p10_min * 60
        sup = allowance[f"{b.block}/{b.direction}"]
        rows.append(
            Residual(
                ref=b.block,
                direction=b.direction,
                kind="loaded" if b.loaded else "empty",
                reference_s=ref,
                physics_s=s,
                supplement_s=sup,
                simulated_s=s + sup,
                residual=round((s + sup - ref) / ref, 4),
                within=within(s + sup, ref),
                wagon_mix=dict(sorted(mix.get((b.block, b.loaded), Counter()).items())),
            )
        )
    physics_within = sum(within(s, b.p10_min * 60) for s, b in zip(phys, targets, strict=True))
    return {"loaded": best[0], "empty": best[1]}, allowance, rows, physics_within


# ---------------------------------------------------------------- passenger


class Leg(NamedTuple):
    """One WTT leg of a train: booked net time and whether it starts or ends at a booked stop."""

    a: str
    b: str
    start_stopped: bool
    end_stopped: bool
    net_s: int  # WTT time net of the engineering and traffic allowances at ``a``
    dep_platform: str | None = None  # booked platform at ``a`` when it is a stop
    arr_platform: str | None = None  # booked platform at ``b`` when it is a stop


def leg_key(a: str, b: str, start_stopped: bool, end_stopped: bool) -> str:
    """Supplement key of a leg in one stopping context, e.g. ``"PUN-PSA/pass-stop"`` (issue #137)."""
    return f"{a}-{b}/{'stop' if start_stopped else 'pass'}-{'stop' if end_stopped else 'pass'}"


def _train_legs(timetable: Timetable, km: dict[str, float]) -> list[tuple[str, int, list[Leg | None]]]:
    """Every train's legs in order (None where a point lies off the section)."""
    out = []
    for svc in timetable.services:
        pts = sorted(timetable.points_of(svc.train_id), key=lambda p: p.seq)
        legs: list[Leg | None] = []
        for p, q in itertools.pairwise(pts):
            if p.op_code not in km or q.op_code not in km:
                legs.append(None)
                continue
            legs.append(
                Leg(
                    p.op_code,
                    q.op_code,
                    p.is_stop,
                    q.is_stop,
                    q.arr_s - p.dep_s - p.eng_allowance_s - p.traffic_allowance_s,
                    booked_platform(p.planned_line) if p.is_stop else None,
                    booked_platform(q.planned_line) if q.is_stop else None,
                )
            )
        out.append((svc.train_no, svc.coaches or 0, legs))
    return out


def _spans(timetable: Timetable, km: dict[str, float], min_legs: int) -> list[Span]:
    """Stop-to-stop spans of at least ``min_legs`` legs; each is its train's own reference (issue #137)."""
    spans = []
    for no, coaches, legs in _train_legs(timetable, km):
        start = None
        for k, leg in enumerate(legs):
            if leg is None:
                start = None
                continue
            if leg.start_stopped:
                start = k
            if leg.end_stopped and start is not None:
                seg = legs[start : k + 1]
                if len(seg) >= min_legs and all(x is not None for x in seg):
                    run = tuple(x for x in seg if x is not None)
                    spans.append(
                        Span(
                            no,
                            coaches,
                            (run[0].a, *(x.b for x in run)),
                            sum(x.net_s for x in run),
                            run[0].dep_platform,
                            run[-1].arr_platform,
                            run,
                        )
                    )
                start = k if leg.end_stopped else None
    return spans


def fit_supplements(
    rows: Sequence[tuple[list[str], float]],
    lower: dict[str, float],
    rounds: int,
    iterations: int,
    robust_s: float,
) -> dict[str, float]:
    """Supplements ``x`` per key minimising sum |sum(x[k] for k in keys) - target| over ``rows``, with
    ``x[k] >= lower[k]``: projected gradient on the least-squares problem, reweighted ``rounds`` times
    towards the absolute error (weights 1 / max(|residual|, robust_s)), so trains carrying extra booked
    time do not pull every supplement up."""
    keys = sorted({k for ks, _ in rows for k in ks})
    idx = {k: i for i, k in enumerate(keys)}
    a = np.zeros((len(rows), len(keys)))
    y = np.array([t for _, t in rows], dtype=float)
    for r, (ks, _) in enumerate(rows):
        for k in ks:
            a[r, idx[k]] += 1
    lo = np.array([lower.get(k, -math.inf) for k in keys])
    x = np.maximum(np.zeros(len(keys)), lo)
    w = np.ones(len(rows))
    for _ in range(rounds):
        aw = a * w[:, None]
        step = 1.0 / np.linalg.norm(aw.T @ a, 2)
        for _ in range(iterations):
            x = np.maximum(x - step * (aw.T @ (a @ x - y)), lo)
        w = 1.0 / np.maximum(np.abs(a @ x - y), robust_s)
    return {k: round(float(x[i]), 1) for k, i in idx.items()}


def booked_platform(planned: Sequence[str | None]) -> str | None:
    """The platform a train is booked on at a stop: the most common over its running days."""
    counts = Counter(x for x in planned if x)
    return min(counts, key=lambda k: (-counts[k], k)) if counts else None


def _passenger(
    runner: Runner, timetable: Timetable, rs: RollingStock, cfg: dict[str, Any]
) -> tuple[float, dict[str, float], list[Residual], dict[str, int]]:
    km = runner.net.km
    tol = float(cfg["span_tolerance_s"])
    default_coaches = int(cfg["passenger_coaches_default"])
    fit_cfg = cfg["supplement_fit"]
    segments = _spans(timetable, km, 1)  # every stop-to-stop segment: the supplements are fitted on them
    spans = _spans(timetable, km, int(cfg["span_min_legs"]))

    def physics(coaches: int, leg: Leg, cap: float, use_approach: bool) -> int:
        dep = runner.net.approach(leg.a, leg.dep_platform) if use_approach else None
        arr = runner.net.approach(leg.b, leg.arr_platform) if use_approach else None
        tpl = rs.passenger(coaches or default_coaches)
        return runner.time(tpl, leg.a, leg.b, leg.start_stopped, leg.end_stopped, cap, dep, arr)

    def supplements(cap: float, use_approach: bool = True) -> dict[str, float]:
        # One supplement per leg, direction and stopping context, fitted jointly on every train's
        # stop-to-stop segments (the WTT's minute rounding shifts time between neighbouring legs, so a
        # leg alone is not a reference). Each leg is bounded below by its physics at line speed, so a
        # negative supplement never beats a speed limit. Per leg, never per span or train (issue #137).
        rows = []
        bound: dict[str, float] = {}
        for sp in segments:
            keys = [leg_key(x.a, x.b, x.start_stopped, x.end_stopped) for x in sp.legs]
            caps = [physics(sp.coaches, x, cap, use_approach) for x in sp.legs]
            fastest = [physics(sp.coaches, x, math.inf, use_approach) for x in sp.legs]
            for k, c, f in zip(keys, caps, fastest, strict=True):
                bound[k] = max(bound.get(k, -math.inf), f - c)
            rows.append((keys, sp.net_s - sum(caps)))
        return fit_supplements(
            rows,
            bound,
            rounds=int(fit_cfg["rounds"]),
            iterations=int(fit_cfg["iterations"]),
            robust_s=float(fit_cfg["robust_s"]),
        )

    def evaluate(
        cap: float, sup: dict[str, float], use_sup: bool, use_approach: bool = True
    ) -> list[Residual]:
        rows = []
        for sp in spans:
            seq = sp.stops
            phys = sum(physics(sp.coaches, leg, cap, use_approach) for leg in sp.legs)
            # A signed supplement may shorten a leg only down to its physics at line speed (no cruise
            # cap), so a calibrated run never beats the speed limits (VER-LSP-01, issue #137).
            s = (
                sum(
                    max(
                        sup.get(leg_key(x.a, x.b, x.start_stopped, x.end_stopped), 0.0),
                        physics(sp.coaches, x, math.inf, use_approach)
                        - physics(sp.coaches, x, cap, use_approach),
                    )
                    for x in sp.legs
                )
                if use_sup
                else 0.0
            )
            ref = float(sp.net_s)
            r = phys + s - ref
            dep = runner.net.approach(seq[0], sp.dep_platform) if use_approach else None
            arr = runner.net.approach(seq[-1], sp.arr_platform) if use_approach else None
            rows.append(
                Residual(
                    ref=f"{seq[0]}-{seq[-1]} ({len(seq) - 1} legs) {sp.train_no}",
                    direction=runner.net.direction(seq[0], seq[-1]),
                    kind="span",
                    reference_s=ref,
                    physics_s=phys,
                    supplement_s=round(s, 1),
                    simulated_s=round(phys + s, 1),
                    residual=round(r, 1),
                    within=abs(r) <= tol,
                    approach=" / ".join(
                        x.label() if x else f"{op} unknown" for x, op in ((dep, seq[0]), (arr, seq[-1]))
                    ),
                )
            )
        return rows

    caps = [float(c) for c in cfg["passenger_caps_kmh"]]
    # As for freight, the cap is chosen on the physics-only fit: signed supplements make the calibrated
    # share almost flat in the cap, so it would not identify a physical cruise speed (issue #137).
    best = max(caps, key=lambda c: (sum(r.within for r in evaluate(c, {}, False)), c))
    sup = supplements(best)
    rows = evaluate(best, sup, True)
    physics_within = sum(r.within for r in evaluate(best, sup, False))
    no_approach = supplements(best, use_approach=False)
    no_approach_within = sum(r.within for r in evaluate(best, no_approach, True, use_approach=False))
    counts = {"total": len(spans), "physics": physics_within, "no_approach": no_approach_within}
    return best, sup, rows, counts


# ---------------------------------------------------------------- checks and report


class ReferenceRun(BaseModel):
    consist: str
    block: str
    from_op: str
    to_op: str
    cap_kmh: float
    seconds: int


def reference_runs(
    runner: Runner, topology: Topology, templates: list[ConsistTemplate], caps: list[float]
) -> list[ReferenceRun]:
    """Pass-through times of every COA block for each template at its cap: the recorded fixture."""
    return [
        ReferenceRun(
            consist=tpl.consist.id,
            block=b.id,
            from_op=b.from_op,
            to_op=b.to_op,
            cap_kmh=cap,
            seconds=runner.time(tpl, b.from_op, b.to_op, False, False, cap),
        )
        for b in topology.block_sections
        if b.level == "coa"
        for tpl, cap in zip(templates, caps, strict=True)
    ]


def check_reference(
    runner: Runner, templates: list[ConsistTemplate], recorded: Sequence[ReferenceRun]
) -> list[str]:
    """Runs whose seconds differ from the recorded fixture by more than the tolerance (none if the
    physics is unchanged). A consist missing from ``templates`` is reported as a mismatch."""
    by_id = {t.consist.id: t for t in templates}
    bad = []
    for r in recorded:
        tpl = by_id.get(r.consist)
        if tpl is None:
            bad.append(f"{r.block} {r.consist}: consist not in the reference templates")
            continue
        now = runner.time(tpl, r.from_op, r.to_op, False, False, r.cap_kmh)
        if abs(now - r.seconds) > REFERENCE_TOLERANCE_S:
            bad.append(f"{r.block} {r.consist}: {now} s, recorded {r.seconds} s")
    return bad


def read_reference(path: Path = REFERENCE_FILE) -> list[ReferenceRun]:
    if not path.exists():
        return []
    return [ReferenceRun.model_validate(x) for x in json.loads(path.read_text(encoding="utf-8"))["runs"]]


def write_reference(runs: Sequence[ReferenceRun], path: Path = REFERENCE_FILE) -> None:
    payload = {"runs": [r.model_dump() for r in runs]}
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8", newline="\n")


def pass_vs_stop(
    runner: Runner, topology: Topology, templates: list[ConsistTemplate], caps: list[float]
) -> list[str]:
    """Blocks where passing through is slower than starting and stopping (must be none)."""
    bad = []
    for b in topology.block_sections:
        if b.level != "coa":
            continue
        for tpl, cap in zip(templates, caps, strict=True):
            through = runner.time(tpl, b.from_op, b.to_op, False, False, cap)
            stop_start = runner.time(tpl, b.from_op, b.to_op, True, True, cap)
            if through > stop_start:
                bad.append(f"{b.id} {tpl.consist.id}: pass {through} s > stop-start {stop_start} s")
    return bad


def calibrate(
    topology: Topology,
    speeds: Speeds,
    speeds_report: Stage4Report,
    demand: FreightDemand,
    timetable: Timetable,
    register: Register | None = None,
    gradients: list[Gradient] | None = None,
    record_reference: bool = False,
    platforms: Sequence[Platform] = (),
) -> tuple[Calibration, CalibrationReport]:
    reg = register or load()
    cfg = reg.value("runtime.calibration")
    rs = RollingStock(reg, topology.network_version)
    network = Network(
        topology, speeds, speeds_report, gradients if gradients is not None else read_gradients(), platforms
    )
    runner = Runner(network, reg)

    f_caps, allowance, f_rows, f_phys = _freight(runner, demand, rs, cfg)
    p_cap, legs, p_rows, counts = _passenger(runner, timetable, rs, cfg)
    wagon = str(cfg["freight_reference_wagon"])
    templates = [
        rs.freight(wagon, True),
        rs.freight(wagon, False),
        rs.passenger(int(cfg["passenger_coaches_default"])),
    ]
    bad = pass_vs_stop(runner, topology, templates, [f_caps["loaded"], f_caps["empty"], p_cap])

    # Reproducibility: the physics reproduces the recorded expected seconds (fixture), so a change to
    # any constant, curve or gradient that moves a run shows up here.
    caps = [f_caps["loaded"], f_caps["empty"], p_cap]
    if record_reference:
        write_reference(reference_runs(runner, topology, templates, caps))
    recorded = read_reference()
    mismatches = check_reference(Runner(network, reg), templates, recorded)

    f_within = sum(r.within for r in f_rows)
    s_within = sum(r.within for r in p_rows)
    report = CalibrationReport(
        freight_caps_kmh=f_caps,
        passenger_cap_kmh=p_cap,
        freight_blocks=len(f_rows),
        freight_physics_within=f_phys,
        freight_within=f_within,
        freight_share=round(f_within / len(f_rows), 4) if f_rows else 0.0,
        spans_total=counts["total"],
        spans_scored=len(p_rows),
        spans_physics_within=counts["physics"],
        spans_no_approach_within=counts["no_approach"],
        spans_within=s_within,
        spans_share=round(s_within / len(p_rows), 4) if p_rows else 0.0,
        freight_target_met=bool(f_rows) and f_within / len(f_rows) >= float(cfg["freight_share"]),
        passenger_target_met=bool(p_rows) and s_within / len(p_rows) >= float(cfg["span_share"]),
        pass_slower_than_stop=bad,
        reference_runs=len(recorded),
        reference_mismatches=mismatches[:WORST],
        reproducible=bool(recorded) and not mismatches,
        worst_blocks=sorted(f_rows, key=lambda r: -abs(r.residual))[:WORST],
        freight_rows=sorted(f_rows, key=lambda r: (r.ref, r.direction, r.kind)),
        worst_spans=sorted(p_rows, key=lambda r: -abs(r.residual))[:WORST],
        span_rows=sorted(p_rows, key=lambda r: (r.ref, r.direction)),
    )
    calibration = Calibration(
        freight_caps_kmh=f_caps, passenger_cap_kmh=p_cap, block_allowance_s=allowance, leg_supplement_s=legs
    )
    return calibration, report


def write(calibration: Calibration, report: CalibrationReport, path: Path = CALIBRATION_FILE) -> None:
    payload = {"calibration": calibration.model_dump(), "report": report.model_dump()}
    path.write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")


def render_markdown(report: CalibrationReport) -> str:
    def table(rows: list[Residual], unit: str) -> list[str]:
        out = [
            "| Reference | Dir | Kind | Reference (s) | Physics (s) | Supplement (s) | Simulated (s) "
            f"| Residual ({unit}) |",
            "|---|---|---|---:|---:|---:|---:|---:|",
        ]
        for r in rows:
            res = f"{r.residual:+.1%}" if unit == "%" else f"{r.residual:+.0f}"
            out.append(
                f"| {r.ref} | {r.direction} | {r.kind} | {r.reference_s:.0f} | {r.physics_s} | "
                f"{r.supplement_s:.0f} | {r.simulated_s:.0f} | {res} |"
            )
        return out

    def mix(r: Residual) -> str:
        total = sum(r.wagon_mix.values())
        parts = sorted(r.wagon_mix.items(), key=lambda kv: (-kv[1], kv[0]))
        return ", ".join(f"{fam} {n / total:.0%}" for fam, n in parts) if total else "reference wagon"

    mix_rows = [
        "| Block | Dir | Kind | Loads | Wagon mix (share of loads) | Model p10 (s) | Log p10 (s) | Within |",
        "|---|---|---|---:|---|---:|---:|:---:|",
        *(
            f"| {r.ref} | {r.direction} | {r.kind} | {sum(r.wagon_mix.values())} | {mix(r)} | "
            f"{r.physics_s} | {r.reference_s:.0f} | {'yes' if r.within else 'no'} |"
            for r in report.freight_rows
        ),
    ]

    lines = [
        "# Running-time calibration report (issue #16, DSC-10)",
        "",
        "Generated by `python -m raildss_twin calibrate --write`. For SME review.",
        "",
        "## Summary",
        "",
        f"- Freight caps: loaded {report.freight_caps_kmh['loaded']:.0f} km/h, "
        f"empty {report.freight_caps_kmh['empty']:.0f} km/h. "
        f"Passenger cap {report.passenger_cap_kmh:.0f} km/h.",
        f"- Freight blocks (>= 30 samples): {report.freight_within}/{report.freight_blocks} "
        f"within +-15 % of the log p10 ({report.freight_share:.1%}); "
        f"physics alone {report.freight_physics_within}.",
        f"- Passenger spans (>= 3 legs): {report.spans_scored} in the WTT, each scored against its own "
        f"train's booked net time (DR-0005, issue #137). Within +-1 min: {report.spans_within} "
        f"({report.spans_share:.1%}); physics alone {report.spans_physics_within}; "
        f"without the station-line approach {report.spans_no_approach_within}. "
        f"Target {'met' if report.passenger_target_met else 'not met'}: the residual is explained "
        "in [passenger-residual.md](passenger-residual.md).",
        f"- Pass-through slower than stop-start: {len(report.pass_slower_than_stop)} blocks. "
        f"Physics reproduces the {report.reference_runs} recorded reference runs "
        f"(`{REFERENCE_FILE.name}`) to {REFERENCE_TOLERANCE_S} s: {'yes' if report.reproducible else 'no'}.",
        "",
        "## Worst 10 freight blocks",
        "",
        *table(report.worst_blocks, "%"),
        "",
        "## Wagon mix per freight block",
        "",
        "Each block is modelled with the wagon families of the loads that crossed it in the log (loaded or "
        "empty as the row), each on its own consist template. The model p10 is the load-weighted 10th "
        "percentile of the families' physics times, before the block allowance (issue #60).",
        "",
        *mix_rows,
        "",
        "## Worst 10 passenger spans",
        "",
        *table(report.worst_spans, "s"),
        "",
        "## Passenger spans by stop pattern",
        "",
        "Stop-to-stop runs leave from and are received on the booked platform's station line (WTT planned "
        "line, via the platform table) at its line speed: reception over the line's CSR before the stop, "
        "departure until the rear clears (issue #59). Each span's reference is its own train's booked net "
        "time; a span is the sum of its legs, each physics plus the signed supplement of the leg, direction "
        "and stopping context (issue #137).",
        "",
        *_span_groups(report.span_rows),
        "",
        "## Every scored passenger miss",
        "",
        "| Span | Train | Dir | Station lines (departure / reception) | Reference (s) | Physics (s) "
        "| Supplement (s) | Simulated (s) | Residual (s) |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
        *(
            f"| {r.ref.rsplit(' ', 1)[0]} | {r.ref.rsplit(' ', 1)[1]} | {r.direction} | {r.approach} | "
            f"{r.reference_s:.0f} | {r.physics_s} | {r.supplement_s:.0f} | {r.simulated_s:.0f} | "
            f"{r.residual:+.0f} |"
            for r in report.span_rows
            if not r.within
        ),
        "",
    ]
    return "\n".join(lines)


def _span_groups(rows: list[Residual]) -> list[str]:
    """One row per stop pattern: spans, within, mean and range of the residual."""
    groups: dict[tuple[str, str], list[Residual]] = defaultdict(list)
    for r in rows:
        groups[(r.ref.rsplit(" ", 1)[0], r.direction)].append(r)
    out = [
        "| Span | Dir | Booked net (s) | Spans | Within | Mean residual (s) | Range (s) |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for (span, direction), rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        res = [r.residual for r in rs]
        out.append(
            f"| {span} | {direction} | {min(r.reference_s for r in rs):.0f} .. "
            f"{max(r.reference_s for r in rs):.0f} | {len(rs)} | {sum(r.within for r in rs)} | "
            f"{statistics.mean(res):+.0f} | {min(res):+.0f} .. {max(res):+.0f} |"
        )
    return out


def frame(rows: list[Residual]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


REPORT_DOC = REPO / "docs" / "twin" / "runtime-calibration.md"


def run(
    register: Register | None = None, record_reference: bool = False
) -> tuple[Calibration, CalibrationReport]:
    """Build the inputs from the KTV-PSA dataset (stages 1-6) and calibrate."""
    from raildss_etl.freight import build_freight
    from raildss_etl.load import run_stage1
    from raildss_etl.speeds import build_speeds
    from raildss_etl.stations import build_station_lines
    from raildss_etl.timetable import build_timetable
    from raildss_etl.topology import build_topology

    tables = run_stage1(write=False)[0]
    topology = build_topology(tables)[0]
    station_lines, platforms, _ = build_station_lines(tables)
    speeds, speeds_report = build_speeds(tables, topology, station_lines)
    demand = build_freight(tables, topology)[0]
    timetable = build_timetable(tables)[0]
    return calibrate(
        topology,
        speeds,
        speeds_report,
        demand,
        timetable,
        register,
        record_reference=record_reference,
        platforms=platforms,
    )
