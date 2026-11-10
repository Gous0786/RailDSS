"""Disruption injector (spec FR-SCN-02, FR-SCN-03, FR-TWIN-15; issue #53).

An :class:`Injector` runs inside a :class:`~raildss_twin.sim.Sim` and applies incidents, scripted up
front or injected interactively (:meth:`Injector.inject`, thread-safe, picked up within one state
period of simulated time), to the ground truth at their stated time and location:

* ``line_block`` (corridor or line block, OHE trip): the block line is taken out of use after the
  train on it has cleared (every sub-block of an automatic line);
* ``point_failure``: a station line (loop) is out of use;
* ``signal_failure``: degraded working, trains pass the line at caution
  (``twin.injector.signal_failure_speed_kmh``);
* ``tsr``: a speed restriction on the line (``speed_kmh``);
* ``loco_failure``: a train stands where it is until the loco is fixed.

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
from raildss_twin.sim import Restriction, Sim

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

    @property
    def end_s(self) -> float:
        return self.start_s + self.duration_s


class Injector:
    def __init__(self, sim: Sim, scripted: Iterable[Disruption] = ()) -> None:
        self.sim = sim
        cfg = sim.register.value("twin.injector")
        self.caution_kmh = float(cfg["signal_failure_speed_kmh"])
        self.sigma_min = float(cfg["default_etr_sigma_min"])
        self.pending: queue.Queue[Disruption] = queue.Queue()
        self.applied: list[Disruption] = []
        for d in scripted:
            self._check(d)
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
        if d.kind == "tsr" and not d.speed_kmh:
            raise ValueError(f"{d.incident_id}: a TSR needs speed_kmh")
        if d.kind == "point_failure" and not any(
            sl.line_no == d.line_no for sl in layout.station_lines.get(d.station or "", [])
        ):
            raise ValueError(f"{d.incident_id}: unknown station line {d.station}/{d.line_no}")
        if d.kind == "loco_failure" and not any(t.plan.run_id == d.run_id for t in self.sim.trains):
            raise ValueError(f"{d.incident_id}: unknown train {d.run_id!r}")

    def _poll(self) -> Proc:
        while True:
            yield self.sim.env.timeout(self.sim.state_period)
            while not self.pending.empty():
                self.sim.env.process(self._run(self.pending.get()))

    def _lines(self, d: Disruption) -> list[BlockLine]:
        assert d.block_id is not None
        return [ln for ln in self.sim.layout.lines[d.block_id] if d.line_id in (None, ln.line_id)]

    def _location(self, d: Disruption) -> tuple[float, str]:
        layout = self.sim.layout
        if d.block_id is not None:
            b = layout.blocks[d.block_id]
            return (layout.km(b.from_op) + layout.km(b.to_op)) / 2, d.block_id
        if d.station is not None:
            return layout.km(d.station), d.station
        train = next(t for t in self.sim.trains if t.plan.run_id == d.run_id)
        return train.km, train.op or "section"

    def _publish(self, d: Disruption, etype: EventType, status: str, etr_s: float) -> None:
        km, ne_id = self._location(d)
        affected = [ln.key for ln in self._lines(d)] if d.block_id else []
        if d.station is not None:
            affected = [f"{d.station}/{d.line_no}"]
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
                **({"cause_code": d.run_id} if d.run_id else {}),
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
        self._publish(d, EventType.DISRUPTION_RAISED, "RAISED", announced)
        held: list[tuple[str, Any]] = []
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
            for ln in self._lines(d):
                self.sim.restrictions.append(Restriction(ln.segment, ln.line_id, start, end, kmh))
        elif d.run_id is not None:
            self.sim.stalls[d.run_id] = (start, end)
        if announced < end:  # the announced ETR passes and the incident goes on: revise it
            yield env.timeout(max(0.0, announced - env.now))
            self._publish(d, EventType.DISRUPTION_UPDATED, "UPDATED", end)
        yield env.timeout(max(0.0, end - env.now))
        for label, req in held:
            self.sim.resources[label].release(req)
            self._mark(label, False)
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
