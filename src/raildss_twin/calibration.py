"""Running-time calibration against the freight log and the WTT (issue #16; spec DSC-10, REC-08).

Two layers, both reported:

1. **Physics.** The point-mass model (:mod:`raildss_twin.running_time`) with one cruise cap per
   class (freight loaded, freight empty, passenger) chosen from ``runtime.calibration`` on the
   physics-only fit.
2. **Supplements.** Location effects the model has no data for (junction and yard approaches,
   signal sighting) are absorbed by non-negative supplements:

   * freight: one allowance per block and direction, shared by loaded and empty trains (one value
     against two references), = max(0, mean residual);
   * passenger: one supplement per WTT leg and direction, fitted on non-stop legs whose time is
     within ``padding_band_s`` of the leg's minimum (single minute-rounded legs are never used as
     minima: the mean over trains is used).

References:

* freight: the log p10 per adjacent block, direction and loaded/empty (``n >= min_samples``),
  simulated pass to pass; target +-``freight_tolerance``;
* passenger: WTT stop-to-stop spans of >= ``span_min_legs`` legs, net of the WTT's engineering and
  traffic allowances, simulated stop to stop (acceleration and braking from physics). The reference
  is the span's standard time (modal net time over trains with the same stops); spans more than
  ``padding_band_s`` above it are padded and reported, not scored. Target +-``span_tolerance_s``.
"""

from __future__ import annotations

import itertools
import json
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
from raildss_schema.entities import BlockSection, BlockSectionLine, Gradient, OperationalPoint, SpeedProfile
from raildss_twin.rolling_stock import ConsistTemplate, RollingStock
from raildss_twin.running_time import Driving, Restriction, Track, simulate

CALIBRATION_FILE = OUT_DIR / "ktv_psa_runtime_calibration.json"
WORST = 10


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


class CalibrationReport(BaseModel):
    freight_caps_kmh: dict[str, float]
    passenger_cap_kmh: float
    freight_blocks: int
    freight_physics_within: int
    freight_within: int
    freight_share: float
    spans_total: int
    spans_padded: int
    spans_scored: int
    spans_physics_within: int
    spans_within: int
    spans_share: float
    spans_within_all: int
    pass_slower_than_stop: list[str]
    reproducible: bool
    worst_blocks: list[Residual]
    worst_spans: list[Residual]

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
    net_s: int  # WTT time net of engineering and traffic allowances


class Calibration(BaseModel):
    freight_caps_kmh: dict[str, float]  # "loaded" / "empty"
    passenger_cap_kmh: float
    block_allowance_s: dict[str, float]  # "A-B/DN"
    leg_supplement_s: dict[str, float]  # "A-B/DN"


# ---------------------------------------------------------------- track


class Network:
    """Per-direction tracks for passenger and goods speed limits, plus station chainage."""

    def __init__(
        self, topology: Topology, speeds: Speeds, speeds_report: Stage4Report, gradients: list[Gradient]
    ) -> None:
        psr = [
            (p.id, p.direction, p.from_km_hwh, p.to_km_hwh, frozenset(p.lines))
            for p in speeds_report.placements
        ]
        self._build(
            topology.operational_points, topology.block_sections, speeds.speed_profiles, psr, gradients
        )

    @classmethod
    def from_entities(
        cls,
        operational_points: Sequence[OperationalPoint],
        block_sections: Sequence[BlockSection],
        speed_profiles: Sequence[SpeedProfile],
        gradients: Sequence[Gradient],
        block_section_lines: Sequence[BlockSectionLine] = (),
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
        return net

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
        self, template: ConsistTemplate, a: str, b: str, start_stopped: bool, end_stopped: bool, cap: float
    ) -> int:
        key = (template.consist.id, a, b, start_stopped, end_stopped, cap)
        if key not in self._cache:
            passenger = template.consist_type == "passenger"
            track = self.net.tracks[(self.net.direction(a, b), "passenger" if passenger else "goods")]
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


def _freight(
    runner: Runner, demand: FreightDemand, rs: RollingStock, cfg: dict[str, Any]
) -> tuple[dict[str, float], dict[str, float], list[Residual], int]:
    targets = [b for b in demand.block_times if b.n >= int(cfg["min_samples"])]
    wagon = str(cfg["freight_reference_wagon"])
    tpl = {True: rs.freight(wagon, True), False: rs.freight(wagon, False)}
    tol = float(cfg["freight_tolerance"])

    def physics(caps: tuple[float, float]) -> list[int]:
        out = []
        for b in targets:
            a, c = b.block.split("-")
            out.append(runner.time(tpl[b.loaded], a, c, False, False, caps[0] if b.loaded else caps[1]))
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
            )
        )
    physics_within = sum(within(s, b.p10_min * 60) for s, b in zip(phys, targets, strict=True))
    return {"loaded": best[0], "empty": best[1]}, allowance, rows, physics_within


# ---------------------------------------------------------------- passenger


def _legs(timetable: Timetable, km: dict[str, float]) -> dict[str, list[float]]:
    legs: dict[str, list[float]] = defaultdict(list)
    for svc in timetable.services:
        pts = sorted(timetable.points_of(svc.train_id), key=lambda p: p.seq)
        for p, q in itertools.pairwise(pts):
            if p.is_stop or q.is_stop or p.op_code not in km or q.op_code not in km:
                continue
            legs[f"{p.op_code}-{q.op_code}"].append(
                q.arr_s - p.dep_s - p.eng_allowance_s - p.traffic_allowance_s
            )
    return legs


def _spans(timetable: Timetable, km: dict[str, float], min_legs: int) -> list[Span]:
    spans = []
    for svc in timetable.services:
        pts = sorted(timetable.points_of(svc.train_id), key=lambda p: p.seq)
        stops = [i for i, p in enumerate(pts) if p.is_stop]
        for i, j in itertools.pairwise(stops):
            seg = pts[i : j + 1]
            if j - i < min_legs or any(p.op_code not in km for p in seg):
                continue
            net = (
                pts[j].arr_s - pts[i].dep_s - sum(p.eng_allowance_s + p.traffic_allowance_s for p in pts[i:j])
            )
            spans.append(Span(svc.train_no, svc.coaches or 0, tuple(p.op_code for p in seg), net))
    return spans


def _modal(values: list[int]) -> int:
    counts = Counter(values)
    top = max(counts.values())
    return min(v for v, n in counts.items() if n == top)


def _passenger(
    runner: Runner, timetable: Timetable, rs: RollingStock, cfg: dict[str, Any]
) -> tuple[float, dict[str, float], list[Residual], dict[str, int]]:
    km = runner.net.km
    band = float(cfg["padding_band_s"])
    tol = float(cfg["span_tolerance_s"])
    default_coaches = int(cfg["passenger_coaches_default"])
    legs = _legs(timetable, km)
    spans = _spans(timetable, km, int(cfg["span_min_legs"]))
    groups: dict[tuple[str, ...], list[Span]] = defaultdict(list)
    for sp in spans:
        groups[sp.stops].append(sp)
    standard = {k: _modal([s.net_s for s in v]) for k, v in groups.items()}
    scored = [s for s in spans if s.net_s <= standard[s.stops] + band]
    reference_tpl = rs.passenger(default_coaches)

    def supplements(cap: float) -> dict[str, float]:
        out = {}
        for key, times in legs.items():
            a, b = key.split("-")
            unpadded = [t for t in times if t <= min(times) + band]
            out[key] = round(
                max(0.0, statistics.mean(unpadded) - runner.time(reference_tpl, a, b, False, False, cap)), 1
            )
        return out

    def evaluate(cap: float, sup: dict[str, float], use_sup: bool) -> list[Residual]:
        rows = []
        for no, coaches, seq, _ in scored:
            tpl = rs.passenger(coaches or default_coaches)
            phys = runner.time(tpl, seq[0], seq[-1], True, True, cap)
            s = sum(sup.get(f"{a}-{b}", 0.0) for a, b in itertools.pairwise(seq)) if use_sup else 0.0
            ref = float(standard[seq])
            r = phys + s - ref
            rows.append(
                Residual(
                    ref=f"{seq[0]}-{seq[-1]} ({len(seq) - 1} legs) {no}",
                    direction=runner.net.direction(seq[0], seq[-1]),
                    kind="span",
                    reference_s=ref,
                    physics_s=phys,
                    supplement_s=round(s, 1),
                    simulated_s=phys + s,
                    residual=round(r, 1),
                    within=abs(r) <= tol,
                )
            )
        return rows

    caps = [float(c) for c in cfg["passenger_caps_kmh"]]
    # The leg supplements depend on the cap, so the cap is chosen on the calibrated fit.
    best = max(caps, key=lambda c: (sum(r.within for r in evaluate(c, supplements(c), True)), c))
    sup = supplements(best)
    rows = evaluate(best, sup, True)
    physics_within = sum(r.within for r in evaluate(best, sup, False))
    within_all = 0
    for _, coaches, seq, net in spans:
        tpl = rs.passenger(coaches or default_coaches)
        sim = runner.time(tpl, seq[0], seq[-1], True, True, best) + sum(
            sup.get(f"{a}-{b}", 0.0) for a, b in itertools.pairwise(seq)
        )
        within_all += abs(sim - net) <= tol
    counts = {
        "total": len(spans),
        "padded": len(spans) - len(scored),
        "physics": physics_within,
        "all": within_all,
    }
    return best, sup, rows, counts


# ---------------------------------------------------------------- checks and report


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
) -> tuple[Calibration, CalibrationReport]:
    reg = register or load()
    cfg = reg.value("runtime.calibration")
    rs = RollingStock(reg, topology.network_version)
    network = Network(
        topology, speeds, speeds_report, gradients if gradients is not None else read_gradients()
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

    # Reproducibility: a fresh runner (no cache) gives the same seconds.
    probe = Runner(network, reg)
    first = f_rows[0]
    a, c = first.ref.split("-")
    reproducible = (
        probe.time(templates[0 if first.kind == "loaded" else 1], a, c, False, False, f_caps[first.kind])
        == first.physics_s
    )

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
        spans_padded=counts["padded"],
        spans_scored=len(p_rows),
        spans_physics_within=counts["physics"],
        spans_within=s_within,
        spans_share=round(s_within / len(p_rows), 4) if p_rows else 0.0,
        spans_within_all=counts["all"],
        freight_target_met=bool(f_rows) and f_within / len(f_rows) >= float(cfg["freight_share"]),
        passenger_target_met=bool(p_rows) and s_within / len(p_rows) >= float(cfg["span_share"]),
        pass_slower_than_stop=bad,
        reproducible=reproducible,
        worst_blocks=sorted(f_rows, key=lambda r: -abs(r.residual))[:WORST],
        worst_spans=sorted(p_rows, key=lambda r: -abs(r.residual))[:WORST],
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
        f"- Passenger spans (>= 3 legs): {report.spans_total} in the WTT, "
        f"{report.spans_padded} padded (more than 1 min above their standard time), "
        f"{report.spans_scored} scored. Within +-1 min: {report.spans_within} "
        f"({report.spans_share:.1%}); physics alone {report.spans_physics_within}; "
        f"against every span, standard or padded: {report.spans_within_all}/{report.spans_total}.",
        f"- Pass-through slower than stop-start: {len(report.pass_slower_than_stop)} blocks. "
        f"Reproducible to 1 s: {'yes' if report.reproducible else 'no'}.",
        "",
        "## Worst 10 freight blocks",
        "",
        *table(report.worst_blocks, "%"),
        "",
        "## Worst 10 passenger spans",
        "",
        *table(report.worst_spans, "s"),
        "",
    ]
    return "\n".join(lines)


def frame(rows: list[Residual]) -> pd.DataFrame:
    return pd.DataFrame([r.model_dump() for r in rows])


REPORT_DOC = REPO / "docs" / "twin" / "runtime-calibration.md"


def run(register: Register | None = None) -> tuple[Calibration, CalibrationReport]:
    """Build the inputs from the KTV-PSA dataset (stages 1-6) and calibrate."""
    from raildss_etl.freight import build_freight
    from raildss_etl.load import run_stage1
    from raildss_etl.speeds import build_speeds
    from raildss_etl.stations import build_station_lines
    from raildss_etl.timetable import build_timetable
    from raildss_etl.topology import build_topology

    tables = run_stage1(write=False)[0]
    topology = build_topology(tables)[0]
    speeds, speeds_report = build_speeds(tables, topology, build_station_lines(tables)[0])
    demand = build_freight(tables, topology)[0]
    timetable = build_timetable(tables)[0]
    return calibrate(topology, speeds, speeds_report, demand, timetable, register)
