"""Completion: synthetic gradients and curves for KTV-PSA (issue #14; spec FR-TWIN-05, §6.4, OD-30).

No public L-section exists for this section (C-14), so both are derived from public data:

1. **Alignment.** OSM ``railway=rail`` ways (no ``service`` tag) form a graph. Each section station
   is anchored on the OSM station/halt node whose ``ref`` is its code. Shortest paths between
   consecutive anchors are joined and resampled every ``step_m``. Chainage is mapped to section km
   and to ``km_ref_hwh`` piecewise-linearly between anchors.
2. **Gradients.** Copernicus GLO-30 (a surface model) is sampled along the alignment. A running
   median removes trees, bridges and cuttings, then the slope over ``segment_m`` windows is the
   gradient. Values beyond the plausibility bound ``max_permille`` [SA] are clipped and counted.
3. **Curves.** Heading change between the bearings of the ``chord_m`` before and after each point
   gives the radius. Runs with radius <= ``max_radius_m`` and length >= ``min_length_m`` are
   curves; degree = 1750 / R (Indian Railways 30.5 m chord convention).
4. **Cross-check.** Every PSR whose reason mentions a curve is matched to the nearest detected curve
   by ``km_ref_hwh``.

All parameters come from ``gradient.derivation`` (SA). Outputs are ``source = synthetic``.
"""

from __future__ import annotations

import heapq
import itertools
import math
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pydantic import BaseModel

from raildss_etl import geo
from raildss_etl.mapping import REPO
from raildss_etl.topology import Topology
from raildss_params import Register, load
from raildss_schema.entities import Curve, Gradient

EARTH_RADIUS_M = 6_371_000.0
M_PER_KM = 1000
PERMILLE = 1000
DEGREE_CHORD_CONSTANT = 1750.0  # R (m) x degree for a 30.5 m (100 ft) chord
OUT_DIR = REPO / "data" / "derived" / "ktv-psa"
DEM_INPUTS = ("dem_N17_E083", "dem_N18_E083", "dem_N18_E084")
PLAIN_CURVE = re.compile(r"\d+(?:\.\d+)?\s*degree curve\.?", re.I)
UNUSED = {"timetable_version", "policy_version", "rule_catalogue_version", "raw"}
REPORT = "ktv_psa_gradients_report.json"


class Point(BaseModel):
    chain_m: float
    km: float
    km_hwh: float
    lat: float
    lon: float
    elev_dem_m: float | None = None
    elev_smooth_m: float | None = None


class PsrCheck(BaseModel):
    psr_id: int
    block: str
    km_hwh_from: float
    km_hwh_to: float
    reason: str
    nearest_curve: str | None
    distance_km: float | None
    running_line_curve: bool  # reason is just "N degree curve"; others name points / crossings in a yard
    confirmed: bool  # a detected curve lies within the match tolerance


class GradientReport(BaseModel):
    alignment_m: float
    points: int
    anchors: dict[str, float]
    anchor_offsets_m: dict[str, float]
    gradient_records: int
    clipped: int
    max_abs_permille: float
    curves: int
    psr_checks: list[PsrCheck]
    psr_match_tolerance_km: float
    blocks_without_profile: list[str]
    segments_without_data: list[str] = []

    @property
    def ok(self) -> bool:
        """Every block has a profile, and every running-line curve PSR has a detected curve nearby."""
        curve_psrs = [c for c in self.psr_checks if c.running_line_curve]
        return (
            not self.blocks_without_profile
            and not self.segments_without_data
            and bool(curve_psrs)
            and all(c.confirmed for c in curve_psrs)
        )


class GradientsAndCurves(BaseModel):
    alignment: list[Point]
    gradients: list[Gradient]
    curves: list[Curve]


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


# ---------------------------------------------------------------- alignment


def _graph(
    osm: dict[str, Any], link_m: float = 0.0
) -> tuple[dict[int, tuple[float, float]], dict[int, list[tuple[int, float]]]]:
    """Rail graph from OSM ways; nodes on parallel tracks within ``link_m`` are linked.

    OSM maps each track of a double or triple line as its own way, with few crossovers. The
    lateral links let the shortest path stay on the corridor whichever track an anchor snaps to.
    """
    coords: dict[int, tuple[float, float]] = {}
    adj: dict[int, list[tuple[int, float]]] = {}
    for el in osm["elements"]:
        if el["type"] != "way":
            continue
        nodes, geom = el["nodes"], el["geometry"]
        for nid, g in zip(nodes, geom, strict=True):
            coords[nid] = (g["lat"], g["lon"])
        for a, b in itertools.pairwise(nodes):
            d = haversine(*coords[a], *coords[b])
            adj.setdefault(a, []).append((b, d))
            adj.setdefault(b, []).append((a, d))
    if link_m > 0:
        cell = link_m / EARTH_RADIUS_M * 180 / math.pi  # degrees of latitude per cell
        grid: dict[tuple[int, int], list[int]] = {}
        for n, (lat, lon) in coords.items():
            grid.setdefault((int(lat // cell), int(lon // cell)), []).append(n)
        for (gy, gx), members in grid.items():
            near = [m for dy in (-1, 0, 1) for dx in (-1, 0, 1) for m in grid.get((gy + dy, gx + dx), [])]
            for n in members:
                for m in near:
                    if m <= n:
                        continue
                    d = haversine(*coords[n], *coords[m])
                    if d <= link_m:
                        adj.setdefault(n, []).append((m, d))
                        adj.setdefault(m, []).append((n, d))
    return coords, adj


def _nearest(coords: dict[int, tuple[float, float]], lat: float, lon: float) -> tuple[int, float]:
    best = min(coords, key=lambda n: haversine(lat, lon, *coords[n]))
    return best, haversine(lat, lon, *coords[best])


def _shortest(adj: dict[int, list[tuple[int, float]]], src: int, dst: int) -> list[int]:
    dist, prev = {src: 0.0}, {}
    heap = [(0.0, src)]
    while heap:
        d, n = heapq.heappop(heap)
        if n == dst:
            break
        if d > dist.get(n, math.inf):
            continue
        for m, w in adj.get(n, []):
            nd = d + w
            if nd < dist.get(m, math.inf):
                dist[m], prev[m] = nd, n
                heapq.heappush(heap, (nd, m))
    if dst not in dist:
        raise ValueError(f"no rail path between OSM nodes {src} and {dst}")
    path = [dst]
    while path[-1] != src:
        path.append(prev[path[-1]])
    return path[::-1]


def build_alignment(
    osm: dict[str, Any], topology: Topology, step_m: float, link_m: float
) -> tuple[list[Point], dict[str, float], dict[str, float]]:
    """Resampled alignment KTV -> PSA, plus station anchor chainages and anchor snap offsets."""
    coords, adj = _graph(osm, link_m)
    refs = {
        el["tags"].get("ref"): (el["lat"], el["lon"])
        for el in osm["elements"]
        if el["type"] == "node" and el["tags"].get("ref")
    }
    ops = sorted(topology.operational_points, key=lambda o: o.km)
    anchors_node: list[int] = []
    offsets: dict[str, float] = {}
    for op in ops:
        if op.code in refs:
            lat, lon = refs[op.code]
        elif op.lat is not None and op.lon is not None:
            lat, lon = op.lat, op.lon
        else:
            raise ValueError(f"{op.code}: no OSM station node and no coordinates")
        node, off = _nearest(coords, lat, lon)
        anchors_node.append(node)
        offsets[op.code] = round(off, 1)

    path: list[int] = [anchors_node[0]]
    anchor_idx = [0]
    for a, b in itertools.pairwise(anchors_node):
        seg = _shortest(adj, a, b)
        path.extend(seg[1:])
        anchor_idx.append(len(path) - 1)

    chain = [0.0]
    for a, b in itertools.pairwise(path):
        chain.append(chain[-1] + haversine(*coords[a], *coords[b]))
    anchor_chain = {op.code: chain[i] for op, i in zip(ops, anchor_idx, strict=True)}

    lats = np.array([coords[n][0] for n in path])
    lons = np.array([coords[n][1] for n in path])
    ch = np.array(chain)
    samples = np.arange(0.0, ch[-1], step_m)
    samples = np.append(samples, ch[-1])
    a_ch = np.array([anchor_chain[o.code] for o in ops])
    km = np.interp(samples, a_ch, [o.km for o in ops])
    hwh = np.interp(samples, a_ch, [o.km_ref_hwh if o.km_ref_hwh is not None else math.nan for o in ops])
    points = [
        Point(
            chain_m=round(float(s), 1),
            km=round(float(k), 4),
            km_hwh=round(float(h), 4),
            lat=round(float(np.interp(s, ch, lats)), 7),
            lon=round(float(np.interp(s, ch, lons)), 7),
        )
        for s, k, h in zip(samples, km, hwh, strict=True)
    ]
    return points, {k: round(v, 1) for k, v in anchor_chain.items()}, offsets


# ---------------------------------------------------------------- profile and gradients


def running_median(values: np.ndarray, window: int) -> np.ndarray:
    half = max(window // 2, 0)
    s = pd.Series(values)
    return s.rolling(2 * half + 1, center=True, min_periods=1).median().to_numpy()


def profile(points: list[Point], dem: geo.Dem, median_window_m: float, step_m: float) -> None:
    raw = np.array([dem.elevation(p.lat, p.lon) for p in points])
    smooth = running_median(raw, round(median_window_m / step_m))
    for p, r, s in zip(points, raw, smooth, strict=True):
        p.elev_dem_m, p.elev_smooth_m = round(float(r), 2), round(float(s), 2)


def _slope_permille(km: np.ndarray, z: np.ndarray) -> float:
    """Least-squares slope in permille. Raises when the points cannot define one (never 0 for no data)."""
    if len(km) < 2 or km[-1] == km[0]:
        raise ValueError(
            f"{len(km)} alignment point(s) in the segment; a slope needs two at different chainages"
        )
    slope = np.polyfit(km * M_PER_KM, z, 1)[0]
    return float(slope * PERMILLE)


def build_gradients(
    points: list[Point], topology: Topology, segment_m: float, max_permille: float
) -> tuple[list[Gradient], int, float, list[str]]:
    """Gradient rows, clipped count, worst raw value, and segments with too few points (no row)."""
    km = np.array([p.km for p in points])
    z = np.array([p.elev_smooth_m for p in points], dtype=float)
    nv = topology.network_version
    km_of = {o.code: o.km for o in topology.operational_points}
    records: list[Gradient] = []
    clipped, worst = 0, 0.0
    undetermined: list[str] = []
    for block in sorted(topology.block_sections, key=lambda b: b.id):
        lo, hi = sorted((km_of[block.from_op], km_of[block.to_op]))
        n = max(1, round((hi - lo) * M_PER_KM / segment_m))
        edges = np.linspace(lo, hi, n + 1)
        for i in range(n):
            mask = (km >= edges[i]) & (km <= edges[i + 1])
            try:
                g = _slope_permille(km[mask], z[mask])
            except ValueError as exc:
                undetermined.append(f"{block.id}-G{i + 1:02d} (km {edges[i]:.3f}-{edges[i + 1]:.3f}): {exc}")
                continue
            worst = max(worst, abs(g))
            if abs(g) > max_permille:
                clipped += 1
                g = math.copysign(max_permille, g)
            records.append(
                Gradient(
                    network_version=nv,
                    id=f"{block.id}-G{i + 1:02d}",
                    block_section_id=block.id,
                    from_km=round(float(edges[i]), 4),
                    to_km=round(float(edges[i + 1]), 4),
                    gradient_permille=round(g, 2),
                    source="synthetic",
                )
            )
    return records, clipped, round(worst, 2), undetermined


# ---------------------------------------------------------------- curves


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.atan2(x, y)


def radii(points: list[Point], chord_m: float, step_m: float) -> np.ndarray:
    """Radius at each point from the heading change across +-chord (inf where straight)."""
    k = max(1, round(chord_m / step_m))
    out = np.full(len(points), np.inf)
    for i in range(k, len(points) - k):
        a, b, c = points[i - k], points[i], points[i + k]
        turn = _bearing(b.lat, b.lon, c.lat, c.lon) - _bearing(a.lat, a.lon, b.lat, b.lon)
        turn = (turn + math.pi) % (2 * math.pi) - math.pi
        # the two chords bend by (chord / R) in total, and each chord spans half of a - c
        arc = (c.chain_m - a.chain_m) / 2
        if abs(turn) > 0:
            out[i] = arc / abs(turn)
    return out


def detect_curves(
    points: list[Point],
    topology: Topology,
    chord_m: float,
    step_m: float,
    max_radius_m: float,
    min_length_m: float,
) -> list[tuple[float, float, float, float, float]]:
    """(km_from, km_to, km_hwh_from, km_hwh_to, min_radius_m) of each detected curve."""
    r = radii(points, chord_m, step_m)
    runs: list[tuple[float, float, float, float, float]] = []
    i = 0
    while i < len(points):
        if r[i] > max_radius_m:
            i += 1
            continue
        j = i
        while j + 1 < len(points) and r[j + 1] <= max_radius_m:
            j += 1
        a, b = points[i], points[j]
        if b.chain_m - a.chain_m >= min_length_m:
            runs.append((a.km, b.km, a.km_hwh, b.km_hwh, float(r[i : j + 1].min())))
        i = j + 1
    return runs


def curve_records(runs: list[tuple[float, float, float, float, float]], topology: Topology) -> list[Curve]:
    km_of = {o.code: o.km for o in topology.operational_points}
    out: list[Curve] = []
    for block in sorted(topology.block_sections, key=lambda b: b.id):
        lo, hi = sorted((km_of[block.from_op], km_of[block.to_op]))
        seq = 0
        for k0, k1, _, _, rmin in runs:
            if k1 < lo or k0 > hi:
                continue
            seq += 1
            out.append(
                Curve(
                    network_version=topology.network_version,
                    id=f"{block.id}-C{seq:02d}",
                    block_section_id=block.id,
                    from_km=round(max(k0, lo), 4),
                    to_km=round(min(k1, hi), 4),
                    radius_m=round(rmin, 0),
                    degree=round(DEGREE_CHORD_CONSTANT / rmin, 2),
                    source="synthetic",
                )
            )
    return out


def psr_checks(
    tables: dict[str, pd.DataFrame], runs: list[tuple[float, float, float, float, float]], tolerance_km: float
) -> list[PsrCheck]:
    out: list[PsrCheck] = []
    for name in ("PSR_DN", "PSR_UP"):
        for rec in tables[name].to_dict("records"):
            reason = str(rec.get("MAVREASON") or "").strip()
            if "curve" not in reason.lower():
                continue
            # MAVFROMSUBKM / MAVTOSUBKM are mast numbers within the km, not fractions: use whole km
            lo, hi = sorted((float(rec["MANFROMKM"]), float(rec["MANTOKM"])))
            hi += 1  # a PSR "km 810-811" reaches the end of km 811
            best: tuple[float, str] | None = None
            for n, (_, _, h0, h1, rmin) in enumerate(runs, 1):
                c_lo, c_hi = sorted((h0, h1))
                d = max(0.0, c_lo - hi, lo - c_hi)
                if best is None or d < best[0]:
                    best = (d, f"curve {n}: km_hwh {c_hi:.2f}-{c_lo:.2f}, R {rmin:.0f} m")
            out.append(
                PsrCheck(
                    psr_id=int(rec["MANBLCKPSRID"]),
                    block=str(rec["MAVBLCKSCTN"]),
                    km_hwh_from=lo,
                    km_hwh_to=hi,
                    reason=reason,
                    nearest_curve=best[1] if best else None,
                    distance_km=round(best[0], 3) if best else None,
                    running_line_curve=bool(PLAIN_CURVE.fullmatch(reason)),
                    confirmed=best is not None and best[0] <= tolerance_km,
                )
            )
    return out


# ---------------------------------------------------------------- pipeline


def build(
    tables: dict[str, pd.DataFrame],
    topology: Topology,
    osm: dict[str, Any],
    dem: geo.Dem,
    register: Register | None = None,
) -> tuple[GradientsAndCurves, GradientReport]:
    reg = register or load()
    p = reg.value("gradient.derivation")
    step = float(p["step_m"])
    points, anchors, offsets = build_alignment(osm, topology, step, float(p["track_link_m"]))
    profile(points, dem, float(p["median_window_m"]), step)
    grads, clipped, worst, undetermined = build_gradients(
        points, topology, float(p["segment_m"]), float(p["max_permille"])
    )
    c = p["curve"]
    runs = detect_curves(
        points, topology, float(c["chord_m"]), step, float(c["max_radius_m"]), float(c["min_length_m"])
    )
    curves = curve_records(runs, topology)
    covered = {g.block_section_id for g in grads}
    report = GradientReport(
        alignment_m=points[-1].chain_m,
        points=len(points),
        anchors=anchors,
        anchor_offsets_m=offsets,
        gradient_records=len(grads),
        clipped=clipped,
        max_abs_permille=worst,
        curves=len(runs),
        psr_checks=psr_checks(tables, runs, float(c["psr_match_km"])),
        psr_match_tolerance_km=float(c["psr_match_km"]),
        segments_without_data=undetermined,
        blocks_without_profile=sorted(b.id for b in topology.block_sections if b.id not in covered),
    )
    return GradientsAndCurves(alignment=points, gradients=grads, curves=curves), report


def write(result: GradientsAndCurves, report: GradientReport, out_dir: Path = OUT_DIR) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / REPORT).write_text(report.model_dump_json(indent=1) + "\n", encoding="utf-8", newline="\n")
    files = {
        "ktv_psa_alignment_derived.csv": [p.model_dump() for p in result.alignment],
        "ktv_psa_gradients_derived.csv": [
            g.model_dump(mode="json", exclude=UNUSED) for g in result.gradients
        ],
        "ktv_psa_curves_derived.csv": [c.model_dump(mode="json", exclude=UNUSED) for c in result.curves],
    }
    paths = []
    for name, rows in files.items():
        path = out_dir / name
        pd.DataFrame(rows).to_csv(path, index=False, lineterminator="\n")
        paths.append(path)
    return [*paths, out_dir / REPORT]


def read_gradients(out_dir: Path = OUT_DIR) -> list[Gradient]:
    df = pd.read_csv(out_dir / "ktv_psa_gradients_derived.csv")
    return [Gradient.model_validate(r) for r in df.to_dict("records")]


def read_curves(out_dir: Path = OUT_DIR) -> list[Curve]:
    df = pd.read_csv(out_dir / "ktv_psa_curves_derived.csv")
    return [
        Curve.model_validate({k: (None if pd.isna(v) else v) for k, v in r.items()})
        for r in df.to_dict("records")
    ]


def run(register: Register | None = None) -> tuple[GradientsAndCurves, GradientReport]:
    from raildss_etl.load import run_stage1
    from raildss_etl.mapping import load_mapping
    from raildss_etl.topology import build_topology

    problems = geo.fetch()
    if problems:
        raise RuntimeError("; ".join(problems))
    mapping = load_mapping()
    tables, _ = run_stage1(mapping, write=False)
    topo, _ = build_topology(tables, mapping)
    osm = geo.load_osm(geo.cached("osm_rail"))
    dem = geo.Dem([geo.cached(n) for n in DEM_INPUTS])
    return build(tables, topo, osm, dem, register)
