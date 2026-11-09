"""Network validator (spec DQ-10, FR-TWIN-02, FR-DATA-04; issue #18).

Runs over the assembled entity tables of one network version. Publication is blocked while it
reports any error (M0 exit criterion: 0 errors).

* **N-01 / DQ-10** every crossing station has at least one loop with CSR > 0;
* **N-02 / DQ-10** every block section and block-section line has a block system and a length > 0;
* **N-03 / DQ-10** routes are consistent: block direction agrees with the chainage order, the
  COA-level blocks chain end to end from the first to the last station in both directions, the
  sums equal the section length, forward and reverse agree within the tolerance, and the
  route-level blocks split at halts tile the COA block they sit in;
* **N-04** references resolve (connections, lines, signals, sub-blocks, speed profiles, gradients,
  curves, timetable, freight);
* **N-05 / FR-DATA-04** every record carries the version stamps in force;
* **N-06** primary keys are unique;
* **N-07** the twin-completion checks of issue #13 (sub-block tiling, braking cover, route coverage)
  and the DQ report (DQ-14..22) are clean;
* **N-08 / DQ-10** coverage: every COA block has gradient records and every block-section line has
  a speed (issue #64).
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel

from raildss_params import Register
from raildss_schema.base import Stamped

M_PER_KM = 1000
BLOCK_SYSTEMS = ("ABS_absolute", "AUTO", "IBS")
# Entities that exist only because of the timetable, and so carry a ``timetable_version``.
TIMETABLE_TABLES = ("service", "timetable_point", "freight_load", "day_instance", "train_run")
MAX_LISTED = 8  # problems listed per rule in the report


class RuleResult(BaseModel):
    id: str
    title: str
    spec: str
    status: Literal["PASS", "FAIL"]
    checked: int
    errors: list[str]
    error_count: int
    notes: list[str] = []


class ValidationReport(BaseModel):
    network_version: str
    timetable_version: str
    rules: list[RuleResult]

    @property
    def error_count(self) -> int:
        return sum(r.error_count for r in self.rules)

    @property
    def ok(self) -> bool:
        return self.error_count == 0


Tables = Mapping[str, Sequence[Stamped]]


def _rule(
    rid: str, title: str, spec: str, checked: int, errors: list[str], notes: list[str] | None = None
) -> RuleResult:
    return RuleResult(
        id=rid,
        title=title,
        spec=spec,
        status="FAIL" if errors else "PASS",
        checked=checked,
        errors=errors[:MAX_LISTED],
        error_count=len(errors),
        notes=notes or [],
    )


def _rows(tables: Tables, name: str) -> list[Any]:
    return list(tables.get(name, ()))


def crossing_stations(tables: Tables) -> tuple[int, list[str], list[str]]:
    """Crossing stations are block stations flagged ``crossing_facility``. A halt is a stopping point
    inside a COA block, with no loop to cross on, so halts are excluded and listed as a note."""
    lines = _rows(tables, "station_line")
    errors: list[str] = []
    ops = _rows(tables, "operational_point")
    crossing = [o for o in ops if o.crossing_facility and not o.is_halt]
    notes = [
        f"{o.code}: halt flagged as a crossing facility in the COA data; not a crossing station"
        for o in ops
        if o.crossing_facility and o.is_halt
    ]
    for op in crossing:
        loops = [x for x in lines if x.op_code == op.code and x.kind == "loop" and (x.csr_m or 0) > 0]
        if not loops:
            errors.append(f"{op.code}: crossing station without a loop with CSR > 0")
    return len(crossing), errors, notes


def block_attributes(tables: Tables) -> tuple[int, list[str]]:
    errors: list[str] = []
    checked = 0
    for table in ("block_section", "block_section_line"):
        for b in _rows(tables, table):
            checked += 1
            name = f"{b.id} ({b.level})" if table == "block_section" else f"{b.block_section_id}/{b.line_id}"
            if b.system not in BLOCK_SYSTEMS:
                errors.append(f"{table} {name}: no block system")
            if not b.length_m > 0:
                errors.append(f"{table} {name}: length {b.length_m} m")
    return checked, errors


def coverage(tables: Tables) -> tuple[int, list[str]]:
    """Every COA-level block has at least one gradient record and every block line a speed > 0."""
    errors: list[str] = []
    graded = {g.block_section_id for g in _rows(tables, "gradient")}
    coa = [b for b in _rows(tables, "block_section") if b.level == "coa"]
    errors += [f"block_section {b.id}: no gradient record" for b in coa if b.id not in graded]
    lines = _rows(tables, "block_section_line")
    errors += [
        f"block_section_line {x.block_section_id}/{x.line_id}: no speed (vmax {x.vmax_kmh} km/h)"
        for x in lines
        if not x.vmax_kmh > 0
    ]
    return len(coa) + len(lines), errors


def _within(a: float, b: float, tol_m: float) -> bool:
    return abs(a - b) <= tol_m


def route_consistency(tables: Tables, section_km: float, tol_km: float) -> tuple[int, list[str]]:
    tol_m = tol_km * M_PER_KM
    ops = sorted(_rows(tables, "operational_point"), key=lambda o: o.seq)
    km = {o.code: o.km for o in ops}
    blocks = _rows(tables, "block_section")
    errors: list[str] = []
    for b in blocks:
        if b.from_op not in km or b.to_op not in km:
            errors.append(f"{b.id} ({b.level}): endpoint is not an operational point")
        elif (km[b.to_op] > km[b.from_op]) != (b.direction == "DN"):
            errors.append(f"{b.id} ({b.level}): direction {b.direction} disagrees with the chainage")
    if errors:
        return len(blocks), errors
    totals: dict[str, float] = {}
    for direction, order in (("DN", ops), ("UP", ops[::-1])):
        coa = {b.from_op: b for b in blocks if b.level == "coa" and b.direction == direction}
        chain, here = [], order[0].code
        while here in coa:
            chain.append(coa[here])
            here = coa[here].to_op
        if here != order[-1].code or len(chain) != len(coa):
            errors.append(f"{direction}: COA blocks do not chain from {order[0].code} to {order[-1].code}")
            continue
        total = sum(b.length_m for b in chain)
        totals[direction] = total
        if not _within(total, section_km * M_PER_KM, tol_m):
            errors.append(f"{direction}: block lengths sum to {total / M_PER_KM:.3f} km, not {section_km} km")
        split = [b for b in blocks if b.level != "coa" and b.direction == direction]
        for parent in chain:
            lo, hi = sorted((km[parent.from_op], km[parent.to_op]))
            pieces = sorted(
                (
                    b
                    for b in split
                    if lo <= min(km[b.from_op], km[b.to_op]) and max(km[b.from_op], km[b.to_op]) <= hi
                ),
                key=lambda b: km[b.from_op] * (1 if direction == "DN" else -1),
            )
            if not pieces:
                continue
            gaps = [a.to_op != b.from_op for a, b in itertools.pairwise(pieces)]
            ends = pieces[0].from_op == parent.from_op and pieces[-1].to_op == parent.to_op
            if any(gaps) or not ends or not _within(sum(p.length_m for p in pieces), parent.length_m, tol_m):
                errors.append(f"{parent.id}: route-level blocks do not tile the COA block")
        covered = {p.id for parent in chain for p in split if _inside(p, parent, km)}
        errors += [f"{b.id}: route-level block outside every COA block" for b in split if b.id not in covered]
    if totals.keys() == {"DN", "UP"} and not _within(totals["DN"], totals["UP"], tol_m):
        errors.append(
            f"forward {totals['DN']} m and reverse {totals['UP']} m differ by more than {tol_km} km"
        )
    return len(blocks), errors


def _inside(piece: Any, parent: Any, km: dict[str, float]) -> bool:
    lo, hi = sorted((km[parent.from_op], km[parent.to_op]))
    return (
        piece.direction == parent.direction
        and lo <= min(km[piece.from_op], km[piece.to_op])
        and (max(km[piece.from_op], km[piece.to_op]) <= hi)
    )


def references(tables: Tables, boundary_blocks: Sequence[str]) -> tuple[int, list[str], list[str]]:
    errors: list[str] = []
    checked = 0
    skipping: Counter[str] = Counter()  # freight path legs that skip stations, per block name

    def need(owner: str, what: str, value: Any, known: set[Any]) -> None:
        nonlocal checked
        checked += 1
        if value not in known:
            errors.append(f"{owner}: {what} {value!r} does not exist")

    ops = {o.code for o in _rows(tables, "operational_point")}
    blocks = {b.id for b in _rows(tables, "block_section")}
    coa = {b.id for b in _rows(tables, "block_section") if b.level == "coa"}
    bsl = {(b.block_section_id, b.line_id) for b in _rows(tables, "block_section_line")}
    sl = {(x.op_code, x.line_no) for x in _rows(tables, "station_line")}
    sub = {(s.block_section_id, s.line_id) for s in _rows(tables, "sub_block")}
    signals = {s.id for s in _rows(tables, "signal")}
    services = {s.train_id for s in _rows(tables, "service")}
    loads = {x.load_id for x in _rows(tables, "freight_load")}
    ends = ops | {part for b in boundary_blocks for part in b.split("-")}
    for b in _rows(tables, "block_section"):
        need(b.id, "from_op", b.from_op, ops)
        need(b.id, "to_op", b.to_op, ops)
    for line in _rows(tables, "block_section_line"):
        need(f"{line.block_section_id}/{line.line_id}", "COA block", line.block_section_id, coa)
    for x in _rows(tables, "station_line"):
        need(f"{x.op_code}/{x.line_no}", "operational point", x.op_code, ops)
    for c in _rows(tables, "connection_edge"):
        owner = f"{c.op_code}/{c.station_line_no} <-> {c.block_section_id}/{c.block_line_id}"
        need(owner, "station line", (c.op_code, c.station_line_no), sl)
        # Connections to the branch blocks that leave the section end at a station of the section.
        if c.block_section_id not in boundary_blocks:
            need(owner, "block line", (c.block_section_id, c.block_line_id), bsl)
    for p in _rows(tables, "platform"):
        need(f"{p.op_code}/{p.platform_no}", "operational point", p.op_code, ops)
    for s in _rows(tables, "speed_profile"):
        if s.block_section_id is not None:
            need(s.id, "block section", s.block_section_id, blocks)
    for g in (*_rows(tables, "gradient"), *_rows(tables, "curve")):
        need(g.id, "block section", g.block_section_id, blocks)
    for sb in _rows(tables, "sub_block"):
        need(
            f"{sb.block_section_id}/{sb.line_id}#{sb.seq}",
            "block line",
            (sb.block_section_id, sb.line_id),
            bsl,
        )
        if sb.entry_signal_id is not None:
            need(f"{sb.block_section_id}/{sb.line_id}#{sb.seq}", "entry signal", sb.entry_signal_id, signals)
    for sg in _rows(tables, "signal"):
        if sg.block_section_id is not None and sg.line_id is not None:
            need(sg.id, "block line", (sg.block_section_id, sg.line_id), bsl | sub)
    for p in _rows(tables, "timetable_point"):
        owner = f"{p.train_id}#{p.seq}"
        need(owner, "service", p.train_id, services)
        need(owner, "operational point", p.op_code, ops)
    for ld in _rows(tables, "freight_load"):
        for what, value in (("entry point", ld.entry_point), ("exit point", ld.exit_point)):
            need(ld.load_id, what, value, ends)
        # The freight log names some blocks that skip stations (R06 §6.6, freight.py): kept as observed.
        skipping.update(bid for bid in ld.path if bid not in blocks)
    for d in _rows(tables, "day_instance"):
        for run in d.run_ids:
            need(d.id, "freight load", run, loads)
    notes = []
    if skipping:
        notes.append(
            f"{sum(skipping.values())} freight path leg(s) in the log name a block that skips stations "
            f"({', '.join(sorted(skipping)[:MAX_LISTED])}, ...): kept as observed, not imposed on the twin"
        )
    return checked, errors, notes


def version_stamps(tables: Tables, network_version: str, timetable_version: str) -> tuple[int, list[str]]:
    errors: list[str] = []
    checked = 0
    for name, rows in tables.items():
        bad: Counter[str] = Counter()
        for r in rows:
            checked += 1
            if r.network_version != network_version:
                bad["network_version"] += 1
            if name in TIMETABLE_TABLES and r.timetable_version != timetable_version:
                bad["timetable_version"] += 1
        errors += [
            f"{name}: {n} record(s) with a wrong or missing {stamp}" for stamp, n in sorted(bad.items())
        ]
    return checked, errors


def unique_keys(tables: Tables) -> tuple[int, list[str]]:
    errors: list[str] = []
    checked = 0
    for name, rows in tables.items():
        if not rows:
            continue
        pk = type(rows[0]).__pk__
        seen = Counter(tuple(getattr(r, f) for f in pk) for r in rows)
        checked += len(rows)
        errors += [f"{name}: duplicate key {k}" for k, n in sorted(seen.items(), key=str) if n > 1]
    return checked, errors


def validate(
    tables: Tables,
    register: Register,
    boundary_blocks: Sequence[str] = (),
    completion_problems: Sequence[str] = (),
    dq_failures: Sequence[str] = (),
) -> ValidationReport:
    nv = str(register.value("network.version"))
    tv = str(register.value("timetable.version"))
    section_km = float(register.value("section.length_km")["total"])
    tol_km = float(register.value("dq.route_reverse_tolerance_km"))
    rules = [
        _rule("N-01", "Every crossing station has a loop with CSR > 0", "DQ-10", *crossing_stations(tables)),
        _rule(
            "N-02",
            "Every block section and line has a system and a length",
            "DQ-10",
            *block_attributes(tables),
        ),
        _rule(
            "N-03",
            "Routes are consistent",
            "DQ-10, FR-TWIN-02",
            *route_consistency(tables, section_km, tol_km),
        ),
        _rule("N-04", "All references resolve", "FR-TWIN-02", *references(tables, boundary_blocks)),
        _rule(
            "N-05", "Every record carries the version stamps", "FR-DATA-04", *version_stamps(tables, nv, tv)
        ),
        _rule("N-06", "Primary keys are unique", "FR-DATA-04", *unique_keys(tables)),
        _rule(
            "N-07",
            "Twin completion and data-quality checks are clean",
            "FR-TWIN-04, DQ-14..22",
            len(completion_problems) + len(dq_failures),
            [*completion_problems, *dq_failures],
        ),
        _rule(
            "N-08", "Every COA block has gradients and every block line a speed", "DQ-10", *coverage(tables)
        ),
    ]
    return ValidationReport(network_version=nv, timetable_version=tv, rules=rules)
