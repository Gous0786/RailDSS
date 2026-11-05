"""ETL stage 4: PSRs and line speeds (spec FR-TWIN-05 data part, R06 App. A.6; issue #9).

* ``SpeedProfile`` (kind ``PSR``) from the two PSR files. Each PSR is placed on its block section
  from its HWH chainage: ``km`` plus a post number (``sub-km``) read as an OHE mast (flag ``O``) or
  a km / telegraph post (flag ``K``). The restriction starts at the *from* reference and runs for
  ``MANDISTANCE`` metres in the direction of travel. The *to* reference is resolved too and its
  difference from that end is reported, never silently absorbed.
* A reference that cannot be resolved (unknown flag, missing or non-numeric post number, a post
  number that does not fit in a km, a PSR outside its block, a block of the wrong direction) fails
  the stage with a message naming the PSR. Nothing is placed approximately.
* Line speeds: every block line and station line must have a speed no higher than the sectional
  maximum. Station lines without a speed (the 3 sidings) get the parameter speed and are listed as
  ``assigned``. Loop and main speeds are checked against ``station_line.speed_kmh``.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel

from raildss_etl.mapping import MappingSpec, load_mapping
from raildss_etl.topology import DEFAULT_OUT, M_PER_KM, Codec, Topology, raw_fields
from raildss_params import Register, load
from raildss_schema.base import Location
from raildss_schema.entities import BlockSection, SpeedProfile, StationLine

DECIMALS = 3  # chainage precision in km (1 m)
PSR_TABLES = {"PSR_DN": "DN", "PSR_UP": "UP"}
REFERENCE_COLUMNS = ("MANFROMKM", "MANTOKM", "MAVFROMSUBKM", "MAVTOSUBKM", "MACKMOHEFLAG")  # kept as given
POST_FLAGS = {"O": "OHE mast", "K": "km post"}


class PsrPlacement(BaseModel):
    id: str
    direction: str
    block_section_id: str
    lines: list[str]
    from_km_hwh: float
    to_km_hwh: float
    from_offset_m: float
    to_offset_m: float
    reference: str
    to_reference_diff_m: float
    outside_block_m: float


class LineSpeed(BaseModel):
    kind: str
    ref: str
    speed_kmh: float
    source: str


class Stage4Report(BaseModel):
    psrs: dict[str, int]
    placements: list[PsrPlacement]
    block_lines: int
    station_lines: int
    assigned_station_lines: list[str]
    sectional_max_kmh: float
    max_speed_kmh: float
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


class Speeds(BaseModel):
    speed_profiles: list[SpeedProfile]
    station_lines: list[StationLine]
    line_speeds: list[LineSpeed]


def _post_km(row: pd.Series, which: str, spacing_m: Decimal, codec: Codec) -> tuple[Decimal, str]:
    """HWH chainage (km) of one end of a PSR, from its km and post number. Raises ValueError."""
    km_col, post_col = ("MANFROMKM", "MAVFROMSUBKM") if which == "from" else ("MANTOKM", "MAVTOSUBKM")
    flag = codec.text(row["MACKMOHEFLAG"])
    if flag not in POST_FLAGS:
        raise ValueError(f"{which} reference has unknown MACKMOHEFLAG {flag!r} (expected O or K)")
    try:
        km, post = codec.number(row[km_col]), codec.number(row[post_col])
    except InvalidOperation as exc:
        raise ValueError(f"{which} reference is not numeric ({row[km_col]!r}/{row[post_col]!r})") from exc
    if km is None or post is None:
        raise ValueError(f"{which} {POST_FLAGS[flag]} reference is missing ({km_col}/{post_col})")
    if post < 0 or post * spacing_m >= M_PER_KM:
        raise ValueError(f"{which} {POST_FLAGS[flag]} number {post} does not fit within km {km}")
    return km + post * spacing_m / M_PER_KM, f"{flag}:{POST_FLAGS[flag]} {post}"


def _place(
    row: pd.Series,
    direction: str,
    blocks: dict[str, BlockSection],
    km_ref: dict[str, float],
    block_lines: dict[str, list[tuple[str, str]]],
    reg: Register,
    codec: Codec,
) -> tuple[SpeedProfile, PsrPlacement]:
    spacing = Decimal(str(reg.value("psr.post_spacing_m")))
    tolerance = float(reg.value("psr.block_end_tolerance_m"))
    bid = str(row["MAVBLCKSCTN"])
    block = blocks.get(bid)
    if block is None:
        raise ValueError(f"block section {bid} is not a COA block section of this network")
    if block.direction != direction:
        raise ValueError(f"block section {bid} runs {block.direction}, but the PSR file is {direction}")
    start_km, start_ref = _post_km(row, "from", spacing, codec)
    end_ref_km, _ = _post_km(row, "to", spacing, codec)
    length = codec.number(row["MANDISTANCE"])
    if length is None or length <= 0:
        raise ValueError("restricted length MANDISTANCE is missing or not positive")

    a, b = km_ref[block.from_op], km_ref[block.to_op]
    sign = 1 if b < a else -1  # +1: chainage falls along the direction of travel (KTV 852.9 -> PSA 676.0)

    def offset(km: Decimal) -> float:
        return float((Decimal(str(a)) - km) * sign * M_PER_KM)

    end_km = start_km - sign * length / M_PER_KM
    o_from, o_to = offset(start_km), offset(end_km)
    over = max(-min(o_from, o_to), max(o_from, o_to) - block.length_m, 0.0)
    if over > tolerance:
        raise ValueError(
            f"lies {over:.0f} m outside block {bid} (limit psr.block_end_tolerance_m = {tolerance:.0f} m)"
        )
    lines = [lid for lid, d in block_lines.get(bid, []) if d in (direction, "BOTH")]
    if not lines:
        raise ValueError(f"block {bid} has no {direction} line")

    def loc(km: Decimal, off: float) -> Location:
        return Location(
            km=float(round(km, DECIMALS)), ne_id=bid, offset_m=round(min(max(off, 0.0), block.length_m), 1)
        )

    pas, gds = codec.number(row["MANPASSPEED"]), codec.number(row["MANGDSSPEED"])
    if pas is None or gds is None or pas <= 0 or gds <= 0:
        raise ValueError("passenger / goods speed is missing or not positive")
    profile = SpeedProfile(
        network_version=reg_nv(reg),
        id=str(row["MANBLCKPSRID"]),
        kind="PSR",
        direction=direction,
        block_section_id=bid,
        line_id=lines[0] if len(lines) == 1 else None,
        location_from=loc(start_km, o_from),
        location_to=loc(end_km, o_to),
        vmax_passenger_kmh=float(pas),
        vmax_goods_kmh=float(gds),
        length_m=float(length),
        reason=codec.text(row["MAVREASON"]),
        time_loss_passenger_s=_opt(codec.number(row["MANTIMELOSSPSGR"])),
        time_loss_goods_s=_opt(codec.number(row["MANTIMELOSSGOODS"])),
        stop_dead=bool(codec.decode("yes_no", row["MACSTOPDEAD"])),
        source="data",
        raw={},
    )
    placement = PsrPlacement(
        id=profile.id,
        direction=direction,
        block_section_id=bid,
        lines=lines,
        from_km_hwh=float(round(start_km, DECIMALS)),
        to_km_hwh=float(round(end_km, DECIMALS)),
        from_offset_m=profile.location_from.offset_m or 0.0,
        to_offset_m=profile.location_to.offset_m or 0.0,
        reference=start_ref,
        to_reference_diff_m=round(float(abs(end_ref_km - end_km) * M_PER_KM), 1),
        outside_block_m=round(over, 1),
    )
    return profile, placement


def _opt(v: Decimal | None) -> float | None:
    return None if v is None else float(v)


def reg_nv(reg: Register) -> str:
    return str(reg.value("network.version"))


def build_speeds(
    tables: dict[str, pd.DataFrame],
    topology: Topology,
    station_lines: list[StationLine],
    mapping: MappingSpec | None = None,
    register: Register | None = None,
) -> tuple[Speeds, Stage4Report]:
    spec = mapping or load_mapping()
    reg = register or load()
    codec = Codec(spec)
    problems: list[str] = []
    vmax = float(reg.value("block.vmax_kmh"))
    km_ref = {o.code: o.km_ref_hwh for o in topology.operational_points if o.km_ref_hwh is not None}
    blocks = {b.id: b for b in topology.block_sections if b.level == "coa"}
    block_lines: dict[str, list[tuple[str, str]]] = {}
    for ln in topology.block_section_lines:
        block_lines.setdefault(ln.block_section_id, []).append((ln.line_id, ln.direction))

    profiles: list[SpeedProfile] = []
    placements: list[PsrPlacement] = []
    counts: dict[str, int] = {}
    for table, direction in PSR_TABLES.items():
        df = tables[table]
        counts[table] = len(df)
        for _, row in df.iterrows():
            try:
                profile, placement = _place(row, direction, blocks, km_ref, block_lines, reg, codec)
                raw = {
                    **raw_fields(row, spec.tables[table]),
                    **{c: codec.text(row[c]) for c in REFERENCE_COLUMNS},
                }
                profile = profile.model_copy(update={"raw": raw})
            except ValueError as exc:
                problems.append(f"PSR {row['MANBLCKPSRID']} ({row['MAVBLCKSCTN']}, {table}): {exc}")
                continue
            if max(profile.vmax_passenger_kmh, profile.vmax_goods_kmh) > vmax:
                problems.append(f"PSR {profile.id}: speed above the sectional maximum {vmax:g} km/h")
            line_speed = min(
                ln.vmax_kmh
                for ln in topology.block_section_lines
                if ln.block_section_id == profile.block_section_id and ln.line_id in placement.lines
            )
            fastest = max(profile.vmax_passenger_kmh, profile.vmax_goods_kmh)
            if fastest > line_speed:
                problems.append(
                    f"PSR {profile.id}: {fastest:g} km/h (passenger {profile.vmax_passenger_kmh:g}, "
                    f"goods {profile.vmax_goods_kmh:g}) is above the line speed {line_speed:g} km/h"
                )
            profiles.append(profile)
            placements.append(placement)
    profiles.sort(key=lambda p: (p.direction or "", p.id))

    # ---------------------------------------------------------------- line speeds
    speeds: list[LineSpeed] = []
    for bl in topology.block_section_lines:
        if not 0 < bl.vmax_kmh <= vmax:
            problems.append(
                f"block line {bl.block_section_id}/{bl.line_id}: speed {bl.vmax_kmh:g} outside (0, {vmax:g}]"
            )
        speeds.append(
            LineSpeed(
                kind="block_line",
                ref=f"{bl.block_section_id}/{bl.line_id}",
                speed_kmh=bl.vmax_kmh,
                source="data",
            )
        )
    siding = float(reg.value("station_line.siding_speed_kmh"))
    expected = reg.value("station_line.speed_kmh")  # {loop: 15, main: [100, 110]}
    updated: list[StationLine] = []
    assigned: list[str] = []
    for sl in station_lines:
        key = f"{sl.op_code}/{sl.line_no}"
        speed, source = sl.line_speed_kmh, "data"
        if not speed:
            if sl.kind != "siding":
                problems.append(
                    f"station line {key}: {sl.kind} line has no speed (MANLINESPEED missing or 0)"
                )
                continue
            speed, source = siding, "assigned"
            assigned.append(f"{key} ({sl.kind})")
            sl = sl.model_copy(update={"line_speed_kmh": speed})
        elif sl.kind == "loop" and speed != float(expected["loop"]):
            problems.append(f"station line {key}: loop speed {speed:g} is not {expected['loop']}")
        elif sl.kind == "main" and speed not in [float(x) for x in expected["main"]]:
            problems.append(f"station line {key}: main speed {speed:g} is not one of {expected['main']}")
        if speed > vmax:
            problems.append(f"station line {key}: speed {speed:g} above the sectional maximum {vmax:g}")
        speeds.append(LineSpeed(kind="station_line", ref=key, speed_kmh=float(speed), source=source))
        updated.append(sl)

    report = Stage4Report(
        psrs=counts,
        placements=placements,
        block_lines=len(topology.block_section_lines),
        station_lines=len(updated),
        assigned_station_lines=assigned,
        sectional_max_kmh=vmax,
        max_speed_kmh=max(s.speed_kmh for s in speeds),
        problems=problems,
    )
    return Speeds(speed_profiles=profiles, station_lines=updated, line_speeds=speeds), report


def write_speeds(speeds: Speeds, report: Stage4Report, out_dir: Path = DEFAULT_OUT) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: dict[str, list[Any]] = {
        "speed_profiles": speeds.speed_profiles,
        "station_lines": speeds.station_lines,
        "line_speeds": speeds.line_speeds,
    }
    for name, items in rows.items():
        (out_dir / f"{name}.jsonl").write_text(
            "".join(json.dumps(i.model_dump(mode="json"), sort_keys=True) + "\n" for i in items),
            encoding="utf-8",
        )
    (out_dir / "speeds_report.json").write_text(
        json.dumps({**report.model_dump(mode="json"), "ok": report.ok}, indent=1, sort_keys=True) + "\n",
        encoding="utf-8",
    )
