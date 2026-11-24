"""Freight detention at section stations (spec FR-TWIN-11, DSC-20, BL-5; FV-02; issue #145).

The freight log shows halts the running-time model alone does not give: crew changes, waits for a path,
long halts. At an intermediate block station of its path a freight train is detained, with the station's
share of calls longer than ``long_min`` (K1 dwell statistics, ``docs/kpi/historical-reference-ktv-psa.json``),
for ``long_min`` plus an exponential tail fitted so that the station's P90 dwell is kept:
``P(dwell > p90) = 0.1``, i.e. the tail's rate is ``-ln(0.1 / share) / (p90 - long_min)``. ``scale``
multiplies the halts (the calibration knob, DSC-20). The draw is seeded by the run seed, the train and the
station, so a run is deterministic per seed and every arm of a comparison (BL-0, the loop arms, the
planner's twin runs and the CP-SAT IR) sees the same halts. ``enabled: false`` runs without them.
"""

from __future__ import annotations

import math
import random

from raildss_params import Register

SECONDS_PER_MIN = 60.0
P90_TAIL = 0.1  # the share of all calls above the P90 dwell


class Detention:
    def __init__(self, register: Register, seed: int) -> None:
        cfg = register.value("twin.freight_detention")
        self.seed = seed
        self.enabled = bool(cfg["enabled"])
        self.long_min = float(cfg["long_min"])
        self.scale = float(cfg["scale"])
        self.stations: dict[str, tuple[float, float]] = {}  # op -> (share of long halts, tail rate per min)
        for op, s in cfg["stations"].items():
            share, p90 = float(s["share"]), float(s["p90_min"])
            if share <= 0:
                continue
            q = P90_TAIL / share
            tail = p90 - self.long_min
            rate = -math.log(q) / tail if q < 1 and tail > 0 else 1 / self.long_min
            self.stations[str(op)] = (min(share, 1.0), rate)

    def seconds(self, run_id: str, op: str) -> float:
        """The detention of freight train ``run_id`` at ``op``, in seconds (0: it does not halt there)."""
        if not self.enabled or op not in self.stations:
            return 0.0
        share, rate = self.stations[op]
        rng = random.Random(f"raildss-detention/{self.seed}/{run_id}/{op}")
        if rng.random() >= share:
            return 0.0
        return (self.long_min + rng.expovariate(rate)) * SECONDS_PER_MIN * self.scale
