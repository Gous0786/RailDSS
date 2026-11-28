# ruff: noqa: E501
"""Evidence for the second face-validity review (issue #171, DSC-11): the checks of round 1 (FV-01..06) on chart exports.

    python tools/face_validity_round2.py --svg docs/review/m2-face-validity.svg --json out.json \
        --day 2025-08-20=web/public/data/2025-08-20.json --day 2025-08-12=<export> \
        --scn SCN-05=web/public/data/SCN-05.json

Reads the chart exports of the twin (BL-0, one real day or a scenario with its disruptions), measures them, and draws one
time-distance panel per chart. For 2025-08-20 it adds the round-1 comparison with the freight log and the WTT.
"""

from __future__ import annotations

import argparse
import json
import statistics
from itertools import pairwise
from pathlib import Path
from typing import Any

import pandas as pd

LOG = Path("data/raw/ktv-psa/KTV-PSA-Infra/WAT_GOODS_TRAIN_AUG25_01082025_27082025.csv")
JUMP_KM = 3.0  # a position jump the twin cannot make in one 30 s tick (FV-01)
DAY_S = 30 * 3600  # the chart shows the day and the early hours of the next
W, PH, ML = 1500, 380, 60
BLUE, RED, GREY = "#1f6feb", "#d1242f", "#aaa"


def load(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def pct(xs: list[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))]


def speeds(t: dict[str, Any]) -> list[float]:
    a = t["actual"]
    return [abs(k1 - k0) / ((t1 - t0) / 3600) for (t0, k0), (t1, k1) in pairwise(a) if t1 > t0 and k1 != k0]


def metrics(d: dict[str, Any]) -> dict[str, Any]:
    """FV-01 jumps and speeds, FV-03 passenger delay tail, and what left the section."""
    intervals = jumps = spikes = 0
    for t in d["trains"]:
        a = t["actual"]
        for (t0, k0), (t1, k1) in pairwise(a):
            if t1 > t0:
                intervals += 1
                jumps += abs(k1 - k0) > JUMP_KM
        for (_, k0), (_, k1), (_, k2) in zip(a, a[1:], a[2:], strict=False):
            spikes += abs(k1 - k0) > JUMP_KM and abs(k2 - k1) > JUMP_KM and abs(k2 - k0) < 1.0
    out: dict[str, Any] = {
        "trains": len(d["trains"]),
        "completed": d["completed"],
        "intervals": intervals,
        "jumps_over_3_km": jumps,
        "single_tick_spikes": spikes,
    }
    for kind in ("passenger", "freight"):
        sp = [v for t in d["trains"] if t["kind"] == kind for v in speeds(t)]
        out[kind] = {
            "n": sum(t["kind"] == kind for t in d["trains"]),
            "speed_median_kmh": round(statistics.median(sp), 1),
            "speed_p95_kmh": round(pct(sp, 0.95), 1),
            "speed_max_kmh": round(max(sp), 1),
        }
    ex = [
        t["exit_delay_s"] / 60
        for t in d["trains"]
        if t["kind"] == "passenger" and t.get("exit_delay_s") is not None
    ]
    if ex:
        out["passenger"].update(
            exit_delay_median_min=round(statistics.median(ex), 1),
            exit_delay_mean_min=round(statistics.fmean(ex), 1),
            exit_delay_max_min=round(max(ex), 1),
            within_5_min=sum(x <= 5 for x in ex),
            over_60_min=sum(x >= 60 for x in ex),
            exited=len(ex),
        )
    out["disruptions"] = [
        {k: x.get(k) for k in ("id", "type", "location", "start_s", "end_s")}
        for x in d.get("disruptions") or []
    ]
    return out


def freight_log(d: dict[str, Any]) -> tuple[dict[str, list[tuple[float, float]]], dict[str, float]]:
    """The freight log of the day's loads: points (seconds, km) per load; the km of each station."""
    km = {s["code"]: s["km"] for s in d["stations"]}
    twin = {t["id"] for t in d["trains"] if t["kind"] == "freight"}
    log = pd.read_csv(LOG, dtype=str, encoding="utf-8-sig")
    log = log[log["LoadId"].isin(twin) & log["Sttn"].isin(km)].copy()
    origin = pd.Timestamp(f"{d['day']} 00:00:00")
    log["arr"] = pd.to_datetime(log["Arrival Time"], errors="coerce", dayfirst=True)
    log["dep"] = pd.to_datetime(log["Depart Time"], errors="coerce", dayfirst=True)
    lines: dict[str, list[tuple[float, float]]] = {}
    for lid, g in log.groupby("LoadId"):
        pts = [
            ((tm - origin).total_seconds(), km[r["Sttn"]])
            for _, r in g.sort_values("arr").iterrows()
            for tm in (r["arr"], r["dep"])
            if pd.notna(tm)
        ]
        if len(pts) > 1:
            lines[str(lid)] = pts
    return lines, km


class Svg:
    def __init__(self, km: dict[str, float], panels: int) -> None:
        self.km = km
        self.top = 0
        self.h = 420
        self.out = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{panels * self.h + 20}" font-family="sans-serif" font-size="11">',
            '<rect width="100%" height="100%" fill="white"/>',
        ]

    def x(self, t: float) -> float:
        return ML + min(max(t, 0), DAY_S) / DAY_S * (W - ML - 20)

    def y(self, k: float) -> float:
        return self.top + PH - 40 - k / max(self.km.values()) * (PH - 60)

    def panel(self, title: str, legend: list[tuple[str, str]]) -> None:
        o = self.out
        o.append(f'<text x="{ML}" y="{self.top + 16}" font-size="14" font-weight="bold">{title}</text>')
        x = ML + 640
        for col, lab in legend:
            o.append(
                f'<line x1="{x}" y1="{self.top + 12}" x2="{x + 24}" y2="{self.top + 12}" stroke="{col}" stroke-width="2"/><text x="{x + 30}" y="{self.top + 16}">{lab}</text>'
            )
            x += 200
        for h in range(0, 31, 2):
            xx = self.x(h * 3600)
            o.append(
                f'<line x1="{xx:.1f}" y1="{self.top + 30}" x2="{xx:.1f}" y2="{self.top + PH - 40}" stroke="#eee"/><text x="{xx - 8:.1f}" y="{self.top + PH - 24}">{h:02d}h</text>'
            )
        for c, k in self.km.items():
            yy = self.y(k)
            o.append(
                f'<line x1="{ML}" y1="{yy:.1f}" x2="{W - 20}" y2="{yy:.1f}" stroke="#ddd"/><text x="8" y="{yy + 4:.1f}">{c}</text>'
            )

    def poly(self, pts: list[Any], col: str, w: float = 1) -> None:
        xy = [(round(self.x(t)), round(self.y(k))) for t, k in pts]
        kept = xy[:1]
        for p in xy[1:-1]:  # drop points within 2 px of the last one kept: the shape stays, the file shrinks
            if abs(p[0] - kept[-1][0]) > 2 or abs(p[1] - kept[-1][1]) > 2:
                kept.append(p)
        xy = kept + xy[-1:]
        if len(xy) > 1:
            self.out.append(
                f'<polyline fill="none" stroke="{col}" stroke-width="{w}" points="{" ".join(f"{a},{b}" for a, b in xy)}"/>'
            )

    def shade(self, t0: float, t1: float, k0: float, k1: float, label: str) -> None:
        x0, x1 = self.x(t0), self.x(t1)
        y0, y1 = self.y(max(k0, k1)), self.y(min(k0, k1))
        self.out.append(
            f'<rect x="{x0:.1f}" y="{y0:.1f}" width="{max(x1 - x0, 2):.1f}" height="{max(y1 - y0, 2):.1f}" fill="#f4b400" fill-opacity="0.35" stroke="#b08000"/>'
        )
        self.out.append(f'<text x="{x0 + 3:.1f}" y="{y0 - 3:.1f}" fill="#7a5800">{label}</text>')

    def next(self) -> None:
        self.top += self.h


def twin_panel(svg: Svg, d: dict[str, Any], title: str) -> None:
    svg.panel(title, [(BLUE, "freight (twin, BL-0)"), (RED, "passenger (twin, BL-0)")])
    for dis in d.get("disruptions") or []:
        if dis.get("km_from") is not None:
            svg.shade(
                dis["start_s"], dis["end_s"], dis["km_from"], dis["km_to"], f"{dis['type']} {dis['location']}"
            )
    for t in d["trains"]:
        svg.poly(t["actual"], BLUE if t["kind"] == "freight" else RED)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", action="append", default=[], help="DAY=export.json (real day)")
    ap.add_argument("--scn", action="append", default=[], help="ID=export.json (disruption scenario)")
    ap.add_argument("--svg")
    ap.add_argument("--json")
    a = ap.parse_args()
    charts = [(k, v, "day") for k, v in (x.split("=", 1) for x in a.day)] + [
        (k, v, "scenario") for k, v in (x.split("=", 1) for x in a.scn)
    ]
    result: dict[str, Any] = {}
    docs = {k: load(v) for k, v, _ in charts}
    for k, d in docs.items():
        result[k] = metrics(d)
    first = docs[charts[0][0]]
    svg = Svg({s["code"]: s["km"] for s in first["stations"]}, panels=len(charts) + 1)
    for i, (k, _, kind) in enumerate(charts):
        d = docs[k]
        if i == 0 and kind == "day":
            lines, _ = freight_log(d)
            svg.panel(
                f"1a. Freight on {k}: log (grey) against the twin (blue), the same {len(lines)} loads",
                [(GREY, "historical log"), (BLUE, "twin (BL-0)")],
            )
            for pts in lines.values():
                svg.poly(pts, GREY)
            for t in d["trains"]:
                if t["kind"] == "freight":
                    svg.poly(t["actual"], BLUE)
            svg.next()
            svg.panel(
                f"1b. Passenger on {k}: booked WTT (grey) against the twin (red)",
                [(GREY, "WTT (booked)"), (RED, "twin (BL-0)")],
            )
            for t in d["trains"]:
                if t["kind"] == "passenger":
                    svg.poly(t["booked"], GREY)
                    svg.poly(t["actual"], RED)
            result[k]["freight_log_loads"] = len(lines)
        else:
            what = "real day" if kind == "day" else "disruption scenario"
            twin_panel(svg, d, f"{i + 1}. {k} ({what}): the twin under BL-0, both kinds of train")
        svg.next()
    svg.out.append("</svg>")
    if a.svg:
        Path(a.svg).write_text("\n".join(svg.out) + "\n", encoding="utf-8", newline="\n")
    if a.json:
        Path(a.json).write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
