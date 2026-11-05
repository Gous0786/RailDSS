"""Point-mass running-time calculator (issue #16; spec FR-TWIN-06, DSC-10, REC-08).

The track is modelled per direction along section chainage (km from KTV): a speed limit and a
gradient per position. A run goes from one chainage to another, starting from rest or at speed and
ending stopped or passing. The integration grid is ``step_m``.

* Speed limit = min(block line speed, consist vmax, cruise cap) and every PSR, held until the rear
  of the train has cleared it (the PSR is extended by the train length in the direction of travel).
* Forward pass: ``m_eff a = u TE(v) - R(v) - m g i`` (traction utilisation ``u``, Davis resistance R,
  gradient i in the direction of travel, rotational mass factor), capped by the speed limit.
* Backward pass: service braking at the template's deceleration towards every lower limit and the
  end condition. The profile is the minimum of both passes; time = sum of 2 dx / (v_i + v_i+1).

Times are rounded to 1 s so results are reproducible to 1 s.
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

import numpy as np

from raildss_twin.rolling_stock import KMH_PER_MPS, ConsistTemplate

GRAVITY = 9.80665  # m/s2 (physical constant)
M_PER_KM = 1000
KG_PER_T = 1000
N_PER_KN = 1000
PERMILLE = 1000
MIN_SPEED_MPS = 0.1  # floor that keeps 2 dx / (v_i + v_i+1) finite at a standing start


@dataclass(frozen=True)
class Restriction:
    """A speed restriction over [from_km, to_km] of section chainage (from_km < to_km)."""

    from_km: float
    to_km: float
    vmax_kmh: float


@dataclass
class Track:
    """One direction of the section. ``increasing`` is True when travel runs towards higher km."""

    increasing: bool
    line_speed: list[Restriction]  # contiguous pieces covering the section
    restrictions: list[Restriction] = field(default_factory=list)  # PSRs (passenger or goods value)
    grade_breaks: list[float] = field(default_factory=list)  # km, sorted
    grade_permille: list[float] = field(default_factory=list)  # rising towards higher km, per piece

    def grade_at(self, km: float) -> float:
        """Gradient in permille, positive = uphill in the direction of travel."""
        if not self.grade_breaks:
            return 0.0
        i = min(max(bisect.bisect_right(self.grade_breaks, km) - 1, 0), len(self.grade_permille) - 1)
        g = self.grade_permille[i]
        return g if self.increasing else -g

    def limit_at(self, km: float, train_length_m: float) -> float:
        lim = math.inf
        for piece in self.line_speed:
            if piece.from_km <= km <= piece.to_km:
                lim = min(lim, piece.vmax_kmh)
        tail = train_length_m / M_PER_KM
        for r in self.restrictions:
            lo, hi = (r.from_km, r.to_km + tail) if self.increasing else (r.from_km - tail, r.to_km)
            if lo <= km <= hi:
                lim = min(lim, r.vmax_kmh)
        return lim


@dataclass(frozen=True)
class Driving:
    """Calibrated driving parameters for one consist type."""

    te_utilisation: float = 1.0
    cruise_kmh: float = math.inf  # cap on the speed actually held
    rotational_mass_factor: float = 1.0  # neutral; calibrated values come from runtime.model


@dataclass(frozen=True)
class Run:
    time_s: int
    distance_m: float
    max_kmh: float


def _te_kn(curve: list[tuple[float, float]], v_kmh: float) -> float:
    speeds = [s for s, _ in curve]
    i = bisect.bisect_right(speeds, v_kmh) - 1
    if i >= len(curve) - 1:
        return curve[-1][1]
    (s0, t0), (s1, t1) = curve[max(i, 0)], curve[i + 1]
    return t0 + (t1 - t0) * (v_kmh - s0) / (s1 - s0)


def simulate(
    track: Track,
    template: ConsistTemplate,
    from_km: float,
    to_km: float,
    start_stopped: bool,
    end_stopped: bool,
    driving: Driving,
    step_m: float,
) -> Run:
    distance = abs(to_km - from_km) * M_PER_KM
    n = max(1, math.ceil(distance / step_m))
    dx = distance / n
    sign = 1 if to_km >= from_km else -1
    kms = [from_km + sign * i * dx / M_PER_KM for i in range(n + 1)]
    cap = min(driving.cruise_kmh, template.consist.vmax_kmh or math.inf)
    vlim = np.array([min(track.limit_at(k, template.length_m), cap) / KMH_PER_MPS for k in kms])

    mass_kg = template.mass_t * KG_PER_T
    m_eff = mass_kg * driving.rotational_mass_factor
    curve = template.loco.te_curve
    v = np.empty(n + 1)
    v[0] = 0.0 if start_stopped else vlim[0]
    for i in range(n):
        v_kmh = v[i] * KMH_PER_MPS
        force_n = (
            driving.te_utilisation * _te_kn(curve, v_kmh) * N_PER_KN
            - template.davis.resistance_kn(v_kmh) * N_PER_KN
            - mass_kg * GRAVITY * track.grade_at(0.5 * (kms[i] + kms[i + 1])) / PERMILLE
        )
        v2 = v[i] * v[i] + 2 * force_n / m_eff * dx
        v[i + 1] = min(math.sqrt(max(v2, MIN_SPEED_MPS**2)), vlim[i + 1])

    b = template.brake_decel_mps2
    w = np.empty(n + 1)
    w[n] = 0.0 if end_stopped else vlim[n]
    for i in range(n - 1, -1, -1):
        w[i] = min(vlim[i], math.sqrt(w[i + 1] * w[i + 1] + 2 * b * dx))
    prof = np.minimum(v, w)
    speeds = np.maximum(prof, MIN_SPEED_MPS)
    time = float(np.sum(2 * dx / (speeds[:-1] + speeds[1:])))
    return Run(
        time_s=round(time), distance_m=round(distance, 1), max_kmh=round(float(prof.max()) * KMH_PER_MPS, 1)
    )
