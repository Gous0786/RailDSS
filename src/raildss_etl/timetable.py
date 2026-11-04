"""ETL stage 5: WTT import and dated expansion (spec FR-TWIN-07, DQ-19, REC-24; issue #10).

* ``Service`` per train version (TRAIN): ICMS train type, priority class (``policy.class_map``),
  Monday-first day mask (journey-origin day), validity, coaches, colour.
* ``TimetablePoint`` per SCHEDULE row, ordered by ``SCHEDULE_SEQ_NO``. A stop is
  ``DEPARTURE > ARRIVAL`` (``HALT_FLAG`` marks halt stations, not stops). ``day_offset`` is the
  departure day (``DAY_OF_RUN - 1``; spec v1.4 DQ-19). Direction is the direction of the row's block.
* Dated expansion: a row is on its block on calendar day *d* when ``BLOCK_BUSY_DAYS[d.weekday()]``
  is set (Monday = 0, R06 App. B); a train starts on origin day *o* when ``DAY_OF_SERVICES[o.weekday()]``
  is set and *o* is inside its validity. ``TrainRun`` ids are ``train_no@origin_date``.
"""

from __future__ import annotations

import datetime as dt
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, cast

import pandas as pd
from pydantic import BaseModel

from raildss_etl.load import SECONDS_PER_DAY, _tz
from raildss_etl.mapping import MappingSpec, load_mapping
from raildss_etl.topology import Codec, raw_fields
from raildss_params import Register, load
from raildss_schema.base import WeekMask
from raildss_schema.entities import Service, TimetablePoint, TrainRun


class Stage5Report(BaseModel):
    services: int
    timetable_points: int
    orphan_points: int
    non_monotonic: list[str]
    nextarvl_mismatches: int
    day_of_run_mismatches: int
    busy_days_mismatches: int
    stops: int
    median_dwell_s: float
    unmapped_train_types: list[str]
    direction_column_differs: int

    @property
    def ok(self) -> bool:
        return not (
            self.orphan_points
            or self.non_monotonic
            or self.nextarvl_mismatches
            or self.day_of_run_mismatches
            or self.busy_days_mismatches
            or self.unmapped_train_types
        )


class Timetable(BaseModel):
    services: list[Service]
    points: list[TimetablePoint]
    busy_days: dict[str, list[bool]]  # "train_id/seq" -> BLOCK_BUSY_DAYS mask (Monday-first)

    def service(self, train_id: str) -> Service:
        return next(s for s in self.services if s.train_id == train_id)

    def points_of(self, train_id: str) -> list[TimetablePoint]:
        return [p for p in self.points if p.train_id == train_id]

    def on_block(self, point: TimetablePoint, day: dt.date) -> bool:
        """True when the train is on this point's block on calendar day ``day``."""
        return self.busy_days[f"{point.train_id}/{point.seq}"][day.weekday()]

    def starts_on(self, service: Service, origin: dt.date) -> bool:
        return service.valid_from <= origin <= service.valid_to and service.days[origin.weekday()]

    def runs_on(self, day: dt.date, tz: dt.tzinfo, network_version: str) -> list[TrainRun]:
        """Train runs with at least one section row on ``day`` (origin date = day - day offset)."""
        by_train: dict[str, list[TimetablePoint]] = defaultdict(list)
        for p in self.points:
            if self.on_block(p, day):
                by_train[p.train_id].append(p)
        runs: list[TrainRun] = []
        for train_id, pts in sorted(by_train.items()):
            svc = self.service(train_id)
            first = min(self.points_of(train_id), key=lambda p: p.seq)
            last = max(self.points_of(train_id), key=lambda p: p.seq)
            origin = day - dt.timedelta(days=min(p.day_offset for p in pts))
            if not self.starts_on(svc, origin):
                continue
            midnight = dt.datetime.combine(origin, dt.time(0), tzinfo=tz)
            runs.append(
                TrainRun(
                    network_version=network_version,
                    run_id=f"{svc.train_no}@{origin.isoformat()}",
                    kind="passenger",
                    run_date=origin,
                    train_id=train_id,
                    entry_point=first.op_code,
                    exit_point=last.op_code,
                    entry_time=midnight + dt.timedelta(seconds=first.arr_s),
                )
            )
        return runs


def _mask(value: Any, wtt: dict[str, Any]) -> list[bool]:
    parts = str(value).split(wtt["list_separator"])
    if len(parts) != len(Service.model_fields["days"].annotation.__args__):  # type: ignore[union-attr]
        raise ValueError(f"day mask must have one value per weekday: {value!r}")
    return [p.strip() == wtt["runs"] for p in parts]


def _text(value: Any, wtt: dict[str, Any]) -> str | None:
    s = str(value).strip()
    return None if s in wtt["no_value"] else s


def _lines(value: Any, wtt: dict[str, Any]) -> list[str | None]:
    if _text(value, wtt) is None:
        return []
    return [_text(v, wtt) for v in str(value).split(wtt["list_separator"])]


def build_timetable(
    tables: dict[str, pd.DataFrame],
    mapping: MappingSpec | None = None,
    register: Register | None = None,
) -> tuple[Timetable, Stage5Report]:
    spec = mapping or load_mapping()
    reg = register or load()
    codec = Codec(spec)
    wtt = spec.wtt
    nv = str(reg.value("network.version"))
    class_map: dict[str, str] = reg.value("policy.class_map")

    unmapped = sorted({str(t) for t in tables["TRAIN"]["TRAIN_TYPE"]} - class_map.keys())
    tr_spec = spec.tables["TRAIN"]
    services = [
        Service(
            network_version=nv,
            train_id=str(r["TRAINID"]),
            train_no=str(r["TRAIN_NUMBER"]),
            name=str(r["TRAIN_NAME"]),
            icms_train_type=str(r["TRAIN_TYPE"]),
            priority_class=class_map.get(str(r["TRAIN_TYPE"]), "unmapped"),
            days=cast(WeekMask, tuple(_mask(r["DAY_OF_SERVICES"], wtt))),  # length checked in _mask
            valid_from=r["VALIDFROM"],
            valid_to=r["VALIDTO"],
            coaches=int(r["NO_OF_COACHES"]) if codec.text(r["NO_OF_COACHES"]) else None,
            reverse_train_no=codec.text(r["REVERSE_TRAIN_NUMBER"]),
            forecolor=codec.text(r["FORECOLOR"]),
            raw=raw_fields(r, tr_spec),
        )
        for _, r in tables["TRAIN"].iterrows()
    ]
    known = {s.train_id for s in services}
    block_dir = {str(r["MAVBLCKSCTN"]): codec.text(r["MAVDRTN"]) for _, r in tables["BLOCKSCTN"].iterrows()}

    sched = tables["SCHEDULE"].copy()
    sched["_seq"] = sched["SCHEDULE_SEQ_NO"].astype(int)
    sched = sched.sort_values(["TRAINID", "_seq"]).reset_index(drop=True)
    sc_spec = spec.tables["SCHEDULE"]
    points: list[TimetablePoint] = []
    busy: dict[str, list[bool]] = {}
    non_monotonic: list[str] = []
    nextarvl_bad = day_bad = busy_bad = dir_differs = orphans = 0
    for train_id, rows in sched.groupby("TRAINID", sort=False):
        if str(train_id) not in known:
            orphans += len(rows)
            continue
        prev_dep: int | None = None
        records = rows.to_dict("records")
        arrivals = [int(x["ARRIVAL"]) for x in records]
        for i, r in enumerate(records):
            arr, dep = int(r["ARRIVAL"]), int(r["DEPARTURE"])
            key = f"{train_id}/{r['_seq']}"
            if dep < arr or (prev_dep is not None and arr < prev_dep):
                non_monotonic.append(key)
            prev_dep = dep
            if i + 1 < len(arrivals) and int(r["NEXTARVL"]) != arrivals[i + 1]:
                nextarvl_bad += 1
            day_of_run = int(r["DAY_OF_RUN"])
            if day_of_run != dep // SECONDS_PER_DAY + 1:
                day_bad += 1
            services_mask, busy_mask = _mask(r["DAY_OF_SERVICES"], wtt), _mask(r["BLOCK_BUSY_DAYS"], wtt)
            shift = dep // SECONDS_PER_DAY
            n = len(services_mask)
            if [services_mask[(d - shift) % n] for d in range(n)] != busy_mask:
                busy_bad += 1
            block = codec.text(r["BLOCK_SCTN"])
            direction = block_dir.get(block or "") or codec.text(r["DIRECTION"])
            if direction != codec.text(r["DIRECTION"]):
                dir_differs += 1
            busy[key] = busy_mask
            points.append(
                TimetablePoint(
                    network_version=nv,
                    train_id=str(train_id),
                    seq=int(r["_seq"]),
                    op_code=str(r["STTN_CODE"]),
                    arr_s=arr,
                    dep_s=dep,
                    day_offset=day_of_run - 1,
                    is_stop=dep > arr,
                    direction=codec.decode("line_direction", direction),
                    block_section_id=block,
                    coa_block_section_id=codec.text(r["COA_BLCK_SCTN"]),
                    eng_allowance_s=int(r["ENGGALWC"] or 0),
                    traffic_allowance_s=int(r["TRFCALWC"] or 0),
                    acc_s=int(r["ACCTIME"] or 0),
                    dec_s=int(r["DECTIME"] or 0),
                    planned_line=_lines(r["PLATFORM"], wtt),
                    booked_crossing_train=codec.text(r["CROSSINGTRAIN"]),
                    crew_change=_text(r["CREWCHANGE"], wtt),
                    traction_change=_text(r["TRTNCODE"], wtt),
                    reversal=bool(codec.decode("yes_no", r["RVRSL"])),
                    raw=raw_fields(pd.Series(r), sc_spec),
                )
            )
    stops = [p for p in points if p.is_stop]
    report = Stage5Report(
        services=len(services),
        timetable_points=len(points),
        orphan_points=orphans,
        non_monotonic=non_monotonic,
        nextarvl_mismatches=nextarvl_bad,
        day_of_run_mismatches=day_bad,
        busy_days_mismatches=busy_bad,
        stops=len(stops),
        median_dwell_s=statistics.median(p.dep_s - p.arr_s for p in stops),
        unmapped_train_types=unmapped,
        direction_column_differs=dir_differs,
    )
    return Timetable(services=services, points=points, busy_days=busy), report


def trains_on_stretches(
    timetable: Timetable, day: dt.date, stretch_of_block: dict[str, str]
) -> dict[tuple[str, str], int]:
    """Distinct trains per (stretch, direction) on calendar day ``day`` (validity-checked)."""
    seen: dict[tuple[str, str], set[str]] = defaultdict(set)
    for p in timetable.points:
        stretch = stretch_of_block.get(p.block_section_id or "")
        if stretch is None or p.direction is None or not timetable.on_block(p, day):
            continue
        svc = timetable.service(p.train_id)
        origin = day - dt.timedelta(days=p.day_offset)
        if timetable.starts_on(svc, origin):
            seen[(stretch, p.direction)].add(p.train_id)
    return dict(sorted(Counter({k: len(v) for k, v in seen.items()}).items()))


def ist(mapping: MappingSpec | None = None) -> dt.tzinfo:
    return _tz((mapping or load_mapping()).timezone)


def trains_per_block(
    timetable: Timetable, day: dt.date, stretch_of_block: dict[str, str]
) -> dict[tuple[str, str, str], int]:
    """Distinct trains per (stretch, direction, COA block) on calendar day ``day``.

    R06 §2's "trains per day per direction" (KTV-VZM 35-41, VZM-PSA ~26-30) matches this per-block
    count; the stretch-wide count (:func:`trains_on_stretches`) is higher on VZM-PSA because some
    trains join or leave the stretch at the NWP junction.
    """
    seen: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for p in timetable.points:
        block = p.coa_block_section_id or ""
        stretch = stretch_of_block.get(block)
        if stretch is None or p.direction is None or not timetable.on_block(p, day):
            continue
        if timetable.starts_on(timetable.service(p.train_id), day - dt.timedelta(days=p.day_offset)):
            seen[(stretch, p.direction, block)].add(p.train_id)
    return {k: len(v) for k, v in sorted(seen.items())}


def write_timetable(timetable: Timetable, report: Stage5Report, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, items in (("services", timetable.services), ("timetable_points", timetable.points)):
        lines = [json.dumps(i.model_dump(mode="json"), sort_keys=True) for i in items]
        (out_dir / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = {**report.model_dump(mode="json"), "ok": report.ok}
    (out_dir / "wtt_report.json").write_text(
        json.dumps(payload, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
