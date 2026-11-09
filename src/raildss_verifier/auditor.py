"""Offline rule auditor over ground-truth traces (spec FR-VER-12, §9.5 item 6).

The auditor rebuilds what was *executed* from a ground-truth event stream and runs the same rule
catalogue over it, so "executed violations = 0" (the G1 KPI) is judged by the independent checker,
not by the simulator that produced the trace.

Input is a stream of :class:`EventEnvelope` (``gt.*``):

* ``TDS_OCCUPANCY`` with ``tds_id`` ``SL:<op>/<line>`` (station line), ``SB:<segment>/<line>#<k>``
  (automatic sub-block, ``k`` in the physical numbering) or ``BL:<segment>/<line>`` (block line);
* ``ROUTE_SET`` with ``mode: send`` and the ``block`` / ``block_line`` the train is despatched on;
* ``DELAY_RECORD`` with ``kind: departure`` at a booked stop.

Train attributes the trace does not carry (kind, length, traction, booked departures) come from
``trains``, keyed by the trace's ``train_run_id``.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Mapping

from pydantic import BaseModel, Field

from raildss_common.envelope import EventEnvelope, EventType
from raildss_params import Register, load
from raildss_verifier import catalogue as cat
from raildss_verifier.engine import check
from raildss_verifier.model import (
    BlockRun,
    CandidatePlan,
    Report,
    StationCall,
    Status,
    SubRun,
    TrainPlan,
)
from raildss_verifier.network import Network

STATION_LINE = "SL:"
SUB_BLOCK = "SB:"


class TrainMeta(BaseModel):
    kind: str
    length_m: float = Field(gt=0)
    electric: bool = True
    booked_dep: dict[str, float] = Field(default_factory=dict)  # op -> booked departure, s since origin


class AuditReport(BaseModel):
    """FR-VER-12 daily figures: executed violations (G1 KPI, target 0) and the breakdown by rule."""

    trains: int
    executed_violations: int
    warnings: int
    by_rule: dict[str, int]
    report: Report


class _Builder:
    def __init__(self, run_id: str, meta: TrainMeta, net: Network, standing_min_s: float) -> None:
        self.run_id, self.meta, self.net, self.standing_min_s = run_id, meta, net, standing_min_s
        self.calls: list[StationCall] = []
        self.runs: list[BlockRun] = []
        self.open_call: StationCall | None = None
        self.open_run: BlockRun | None = None
        self.first_sub: tuple[int, float] | None = None
        self.booked_stops: set[int] = set()
        self.last_s = 0.0

    def occupied(self, label: str, t: float) -> None:
        if label.startswith(STATION_LINE):
            op, _, no = label.removeprefix(STATION_LINE).rpartition("/")
            if self.open_run is not None:
                self._close_run(t)
            self.open_call = StationCall(op=op, line_no=int(no), arr_s=t, dep_s=t)
            self.calls.append(self.open_call)
            return
        if label.startswith(SUB_BLOCK):
            canonical = int(label.rpartition("#")[-1])
            run = self.open_run
            if run is None:  # the first sub-block is taken just before the despatch route is set
                self.first_sub = (canonical, t)
                return
            if run.sub_blocks:
                run.sub_blocks[-1].clear_s = t
            seq = self.net.sub_block_seq(run.block_id, run.line_id, canonical)
            run.sub_blocks.append(SubRun(seq=seq, enter_s=t, clear_s=t))

    def released(self, label: str, t: float) -> None:
        call = self.open_call
        if call is not None and label == f"{STATION_LINE}{call.op}/{call.line_no}":
            call.dep_s = t  # leaves the section (a despatched call is closed by its route)
            self.open_call = None

    def despatched(self, block: str, line: str, t: float) -> None:
        if self.open_call is not None:
            self.open_call.dep_s = t
            self.open_call = None
        self.open_run = BlockRun(block_id=block, line_id=line, enter_s=t, clear_s=t)
        if self.first_sub is not None:
            canonical, t0 = self.first_sub
            seq = self.net.sub_block_seq(block, line, canonical)
            self.open_run.sub_blocks.append(SubRun(seq=seq, enter_s=t0, clear_s=t0))
            self.first_sub = None

    def departed(self, op: str) -> None:
        if self.open_call is not None and self.open_call.op == op:
            self.booked_stops.add(len(self.calls) - 1)

    def _close_run(self, t: float) -> None:
        assert self.open_run is not None
        self.open_run.clear_s = t
        if self.open_run.sub_blocks:
            self.open_run.sub_blocks[-1].clear_s = t
        self.runs.append(self.open_run)
        self.open_run = None

    def build(self) -> TrainPlan:
        if self.open_call is not None:  # still on a line at the end of the trace
            self.open_call.dep_s = max(self.open_call.arr_s, self.last_s)
        last = len(self.calls) - 1
        for i, c in enumerate(self.calls):
            booked = i in self.booked_stops
            held = i < last and c.dep_s - c.arr_s >= self.standing_min_s
            c.stop = booked or held
            if booked:
                c.booked_dep_s = self.meta.booked_dep.get(c.op)
        kind = self.meta.kind if self.meta.kind in ("passenger", "freight") else "other"
        return TrainPlan(
            run_id=self.run_id,
            kind=kind,  # type: ignore[arg-type]
            length_m=self.meta.length_m,
            electric=self.meta.electric,
            calls=self.calls,
            runs=self.runs[: max(len(self.calls) - 1, 0)],
        )


def plan_from_trace(
    events: Iterable[EventEnvelope],
    trains: Mapping[str, TrainMeta],
    net: Network,
    origin: dt.datetime,
    register: Register | None = None,
    plan_id: str = "ground-truth",
) -> CandidatePlan:
    """Rebuild the executed movements of every train in ``trains`` as a :class:`CandidatePlan`."""
    register = register or load()
    standing = float(register.value("ver.standing_min_s"))
    builders: dict[str, _Builder] = {}
    for ev in sorted(events, key=lambda e: e.seq):
        rid = ev.train_run_id
        if rid is None or rid not in trains:
            continue
        b = builders.get(rid)
        if b is None:
            b = builders[rid] = _Builder(rid, trains[rid], net, standing)
        t = (ev.event_time - origin).total_seconds()
        b.last_s = t
        p = ev.payload
        if ev.event_type is EventType.TDS_OCCUPANCY:
            (b.occupied if p["occupied"] else b.released)(str(p["tds_id"]), t)
        elif ev.event_type is EventType.ROUTE_SET and p.get("mode") == "send":
            b.despatched(str(p["block"]), str(p["block_line"]), t)
        elif ev.event_type is EventType.DELAY_RECORD and p.get("kind") == "departure":
            b.departed(str(p["station"]))
    return CandidatePlan(
        plan_id=plan_id,
        network_version=net.version,
        trains=[b.build() for _, b in sorted(builders.items()) if b.calls],
    )


def audit(
    events: Iterable[EventEnvelope],
    trains: Mapping[str, TrainMeta],
    net: Network,
    origin: dt.datetime,
    catalogue: cat.Catalogue | None = None,
    register: Register | None = None,
    plan_id: str = "ground-truth",
) -> AuditReport:
    """Run the catalogue over a ground-truth trace and count executed violations by rule."""
    register = register or load()
    plan = plan_from_trace(events, trains, net, origin, register, plan_id)
    report = check(plan, net, catalogue, register)
    by_rule = {r.rule_id: len(r.violations) for r in report.results if r.violations}
    return AuditReport(
        trains=len(plan.trains),
        executed_violations=sum(
            len(r.violations) or 1 for r in report.results if r.status in (Status.FAIL, Status.ERROR)
        ),
        warnings=sum(len(r.violations) for r in report.results if r.status is Status.WARN),
        by_rule=by_rule,
        report=report,
    )
