"""Disruption injector (spec FR-SCN-02, FR-SCN-03, FR-TWIN-15; issue #53).

An :class:`Injector` runs inside a :class:`~raildss_twin.sim.Sim` and applies incidents, scripted up
front or injected interactively (:meth:`Injector.inject`, thread-safe, picked up within one state
period of simulated time), to the ground truth at their stated time and location:

* ``line_block`` (corridor or line block, OHE trip): the block line is taken out of use after the
  train on it has cleared (every sub-block of an automatic line). With ``through_block`` it covers a range
  of block sections, and with ``single_line_working`` the other line of a double-line section is worked
  in both directions meanwhile, trains crossing at stations (FR-TWIN-18);
* ``point_failure``: a station line (loop) is out of use;
* ``signal_failure``: degraded working, trains pass the line at caution
  (``twin.injector.signal_failure_speed_kmh``). With ``block_instrument`` the absolute-block instruments
  have failed too: every train entering the block waits ``twin.injector.paper_line_clear_min`` for a
  written authority (paper line clear) before it goes (issue #146, SCN-07);
* ``tsr``: a speed restriction on the line (``speed_kmh``);
* ``loco_failure``: the train's loco fails in the block section it is running in (a scripted failure is
  known to the ground truth from the start, so the leg running at its start time stalls) and the train
  stands there, blocking its line, until relief. It is announced once the train is in a block, with that
  line affected; with ``single_line_working`` the other line is worked both ways until relief
  (issue #146, SCN-04).

The DSS never sees the ground truth. It learns of an incident only from ``ops.disruptions``:
``DISRUPTION_RAISED`` with the announced ETR and its uncertainty, ``DISRUPTION_UPDATED`` when the
announced ETR passes and the incident goes on (the ETR is revised to the real end), and
``DISRUPTION_CLEARED`` when it ends.
"""

from __future__ import annotations

import datetime as dt
import queue
from collections.abc import Generator, Iterable
from typing import Any, Literal

from pydantic import BaseModel, Field

from raildss_common.envelope import EventType
from raildss_twin.layout import BlockLine
from raildss_twin.sim import Restriction, Sim, token

Proc = Generator[Any, Any, Any]
OPS_SOURCE = "SIM.CONTROL_LOG"
TOPIC = "ops.disruptions"
SECONDS_PER_MIN = 60

Kind = Literal["line_block", "point_failure", "signal_failure", "tsr", "loco_failure"]


class Disruption(BaseModel):
    incident_id: str = Field(pattern=r"^[A-Za-z0-9._:-]+$")
    kind: Kind
    start_s: float
    duration_s: float = Field(gt=0)  # the real duration (ground truth)
    etr_s: float | None = None  # the announced ETR; default: the real end
    etr_sigma_min: float | None = None
    block_id: str | None = None  # line_block, signal_failure, tsr
    line_id: str | None = None  # None: every line of the block
    station: str | None = None  # point_failure
    line_no: int | None = None  # point_failure
    run_id: str | None = None  # loco_failure
    speed_kmh: float | None = None  # tsr
    through_block: str | None = None  # line_block: the range runs from block_id to this block, inclusive
    single_line_working: bool = False  # line_block, loco_failure: work the other line both ways meanwhile
    block_instrument: bool = False  # signal_failure: block instruments failed too (paper line clear)

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


class Injector:
    def __init__(self, sim: Sim, scripted: Iterable[Disruption] = ()) -> None:
        self.sim = sim
        cfg = sim.register.value("twin.injector")
        self.caution_kmh = float(cfg["signal_failure_speed_kmh"])
        self.sigma_min = float(cfg["default_etr_sigma_min"])
        self.plc_s = float(cfg["paper_line_clear_min"]) * SECONDS_PER_MIN
        self.pending: queue.Queue[Disruption] = queue.Queue()
        self.applied: list[Disruption] = []
        self.where: dict[str, tuple[float, str, str]] = {}  # loco failure: km, block, line key
        for d in scripted:
            self._check(d)
            if d.kind == "loco_failure":  # ground truth: the leg running at the start stalls
                sim.stalls[self._train(d).plan.run_id] = (d.start_s, d.end_s)
            sim.env.process(self._run(d))
        sim.env.process(self._poll())

    # ------------------------------------------------------------------ public
    def inject(self, d: Disruption) -> None:
        """Interactive injection (FR-SCN-03): starts at the next poll, at once if ``start_s`` is past."""
        self._check(d)
        self.pending.put(d)

    # ------------------------------------------------------------------ internals
    def _check(self, d: Disruption) -> None:
        layout = self.sim.layout
        if d.kind in ("line_block", "signal_failure", "tsr") and d.block_id not in layout.blocks:
            raise ValueError(f"{d.incident_id}: unknown block section {d.block_id!r}")
        if d.through_block is not None:
            self._range(d)  # raises if the range is not valid
        if d.single_line_working and not (
            (d.kind == "line_block" and d.line_id is not None) or d.kind == "loco_failure"
        ):
            raise ValueError(f"{d.incident_id}: single-line working needs a line_block of one line")
        if d.block_instrument and d.kind != "signal_failure":
            raise ValueError(f"{d.incident_id}: block_instrument is a signal_failure option")
        if d.kind == "tsr" and not d.speed_kmh:
            raise ValueError(f"{d.incident_id}: a TSR needs speed_kmh")
        if d.kind == "point_failure" and not any(
            sl.line_no == d.line_no for sl in layout.station_lines.get(d.station or "", [])
        ):
            raise ValueError(f"{d.incident_id}: unknown station line {d.station}/{d.line_no}")
        if d.kind == "loco_failure":
            self._train(d)  # raises if unknown

    def _train(self, d: Disruption) -> Any:
        """The train of a loco failure: by run id, or by load id (a freight run has no date)."""
        rid = d.run_id or ""
        for t in self.sim.trains:
            if t.plan.run_id in (rid, rid.split("@")[0]):
                return t
        raise ValueError(f"{d.incident_id}: unknown train {d.run_id!r}")

    def _poll(self) -> Proc:
        while True:
            yield self.sim.env.timeout(self.sim.state_period)
            while not self.pending.empty():
                self.sim.env.process(self._run(self.pending.get()))

    def _range(self, d: Disruption) -> list[str]:
        """The block sections of the incident: ``block_id``, or the run from it to ``through_block``."""
        assert d.block_id is not None
        layout = self.sim.layout
        if d.through_block is None:
            return [d.block_id]
        first, last = layout.blocks[d.block_id], layout.blocks.get(d.through_block)
        if last is None or first.direction != last.direction:
            raise ValueError(
                f"{d.incident_id}: {d.through_block!r} is not a block section of {d.block_id}'s direction"
            )
        stations = layout.block_stations()
        i, j = stations.index(first.from_op), stations.index(last.to_op)
        step = 1 if first.direction == "DN" else -1
        if (j - i) * step <= 0:
            raise ValueError(f"{d.incident_id}: {d.through_block} lies before {d.block_id}")
        return [layout.block_by_pair[(stations[k], stations[k + step])].id for k in range(i, j, step)]

    def _lines(self, d: Disruption) -> list[BlockLine]:
        return [
            ln
            for bid in self._range(d)
            for ln in self.sim.layout.lines[bid]
            if d.line_id in (None, ln.line_id)
        ]

    def _location(self, d: Disruption) -> tuple[float, str]:
        layout = self.sim.layout
        if d.block_id is not None:
            b = layout.blocks[d.block_id]
            return (layout.km(b.from_op) + layout.km(b.to_op)) / 2, d.block_id
        if d.station is not None:
            return layout.km(d.station), d.station
        if d.incident_id in self.where:
            km, block, _ = self.where[d.incident_id]
            return km, block
        train = self._train(d)
        return train.km, train.op or "section"

    def _publish(self, d: Disruption, etype: EventType, status: str, etr_s: float) -> None:
        km, ne_id = self._location(d)
        affected = [ln.key for ln in self._lines(d)] if d.block_id else []
        if d.station is not None:
            affected = [f"{d.station}/{d.line_no}"]
        if d.incident_id in self.where:
            affected = [self.where[d.incident_id][2]]
        self.sim.emit(
            TOPIC,
            etype,
            None,
            {
                "incident_id": d.incident_id,
                "type": d.kind,
                "status": status,
                "etr": (self.sim.origin + dt.timedelta(seconds=etr_s)).isoformat(),
                "etr_source": "SM_ESTIMATE",
                "etr_sigma_min": d.etr_sigma_min if d.etr_sigma_min is not None else self.sigma_min,
                "lines_affected": affected,
                **({"cause_code": token(d.run_id)} if d.run_id else {}),  # load ids may contain spaces
            },
            km,
            ne_id,
            source=OPS_SOURCE,
        )

    def _run(self, d: Disruption) -> Proc:
        env = self.sim.env
        yield env.timeout(max(0.0, d.start_s - env.now))
        start = env.now
        end = start + d.duration_s
        announced = d.etr_s if d.etr_s is not None else end
        self.applied.append(d)
        working: list[tuple[str, list[str]]] = []
        if d.kind == "loco_failure":
            train = self._train(d)
            self.sim.stalls[train.plan.run_id] = (d.start_s, end)
            while train.block is None and env.now < end and train.status not in ("done", "stuck"):
                yield env.timeout(self.sim.state_period)  # it fails once it runs in a block section
            if train.block is not None and train.line is not None:
                self.where[d.incident_id] = (train.km, train.block, train.line.key)
                if d.single_line_working:
                    working.append((train.block, self.sim.slw_begin(train.block, train.line.line_id)))
        self._publish(d, EventType.DISRUPTION_RAISED, "RAISED", announced)
        held: list[tuple[str, Any]] = []
        if d.single_line_working and d.kind != "loco_failure":
            assert d.line_id is not None
            for bid in self._range(d):
                working.append((bid, self.sim.slw_begin(bid, d.line_id)))
        if d.kind in ("line_block", "point_failure"):
            labels = (
                [lab for ln in self._lines(d) for lab in self._labels(ln)]
                if d.kind == "line_block"
                else [f"SL:{d.station}/{d.line_no}"]
            )
            for label in labels:  # taken in turn, each once the train on it has cleared
                req = self.sim.resource(label).request(priority=-1)  # ahead of waiting trains
                yield req
                held.append((label, req))
                self._mark(label, True)
        elif d.kind in ("signal_failure", "tsr"):
            kmh = self.caution_kmh if d.kind == "signal_failure" else float(d.speed_kmh or 0)
            extra = self.plc_s if d.block_instrument else 0.0
            for ln in self._lines(d):
                self.sim.restrictions.append(Restriction(ln.segment, ln.line_id, start, end, kmh, extra))
        if announced < end:  # the announced ETR passes and the incident goes on: revise it
            yield env.timeout(max(0.0, announced - env.now))
            self._publish(d, EventType.DISRUPTION_UPDATED, "UPDATED", end)
        yield env.timeout(max(0.0, end - env.now))
        for label, req in held:
            self.sim.resources[label].release(req)
            self._mark(label, False)
        for bid, keys in working:
            line_id = (
                d.line_id if d.kind != "loco_failure" else self.where[d.incident_id][2].rpartition("/")[2]
            )
            assert line_id is not None
            self.sim.slw_end(bid, line_id, keys)
        self._publish(d, EventType.DISRUPTION_CLEARED, "CLEARED", env.now)

    def _labels(self, ln: BlockLine) -> list[str]:
        if ln.system == "AUTO":
            subs = self.sim.layout.canonical_sub_blocks(ln.block_id, ln.line_id)
            if subs:
                return [f"SB:{key}" for key, _ in subs]
        return [f"BL:{ln.key}"]

    def _mark(self, label: str, occupied: bool) -> None:
        """Ground truth of the resource taken out of use (``gt.occupancy`` without a train)."""
        self.sim.emit("gt.occupancy", EventType.TDS_OCCUPANCY, None, {"tds_id": label, "occupied": occupied})
