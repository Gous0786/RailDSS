"""Stochastic perturbations of the baseline simulation (spec FR-TWIN-11; issue #56).

* **Passenger entry delay** (``delay.passenger.entry_profile``): a mixture of an on-time mass
  (uniform over the on-time window, probability by category) and a log-normal tail in minutes. The
  tail is not shifted: the implied share within 15 min is the one in the register (premium 0.79,
  mail/express 0.72, passenger 0.66). Freight enters at its replayed time and is not delayed here.
* **Run-time and dwell noise** (``delay.noise``): a normal factor on every leg (sd in percent, by
  consist kind) and a log-normal factor on a stop, applied only to lengthen it (a booked train never
  leaves before its booked time).

Every draw comes from its own generator seeded by the run seed, the purpose and the train (and leg or
station), so the draws do not depend on the order the simulator asks for them: the same seed gives the
same trace, a different seed a different one.
"""

from __future__ import annotations

import math
import random

from raildss_params import Register

SECONDS_PER_MIN = 60.0
CAUSE_ENTRY = "ENTRY_DELAY"  # cause code of a synthetic primary entry delay


class Perturbations:
    def __init__(self, register: Register, seed: int) -> None:
        self.seed = seed
        profile = register.value("delay.passenger.entry_profile")
        self.p_on_time: dict[str, float] = {k: float(v) for k, v in profile["p_on_time"].items()}
        window = profile["on_time_window_min"]
        self.window_min = (float(window["low"]), float(window["high"]))
        tail = profile["tail_lognormal"]
        self.mu, self.sigma = float(tail["mu_ln_min"]), float(tail["sigma"])
        noise = register.value("delay.noise")
        self.dwell_sigma = float(noise["dwell_lognormal_sigma"])
        self.min_leg = float(noise["min_leg_factor"])
        self.run_sd = {k: float(v) / 100.0 for k, v in noise["run_time_sd_pct"].items()}

    def _rng(self, purpose: str, *key: str) -> random.Random:
        return random.Random("/".join(("raildss-pert", str(self.seed), purpose, *key)))

    def entry_delay_s(self, kind: str, category: str, run_id: str) -> float:
        """Seconds a passenger train enters late (negative: early). Zero for any other kind."""
        if kind != "passenger" or category not in self.p_on_time:
            return 0.0
        rng = self._rng("entry", run_id)
        if rng.random() < self.p_on_time[category]:
            minutes = rng.uniform(*self.window_min)
        else:
            minutes = math.exp(rng.gauss(self.mu, self.sigma))
        return minutes * SECONDS_PER_MIN

    def leg_factor(self, kind: str, run_id: str, a: str, b: str) -> float:
        """Multiplier on the calibrated running time of one leg."""
        sd = self.run_sd.get(kind, 0.0)
        if sd <= 0:
            return 1.0
        return max(self.min_leg, 1.0 + self._rng("leg", run_id, a, b).gauss(0.0, sd))

    def dwell_s(self, base_s: float, run_id: str, op: str) -> float:
        """A stop of ``base_s`` seconds, lengthened by the log-normal noise (never shortened)."""
        factor = math.exp(self._rng("dwell", run_id, op).gauss(0.0, self.dwell_sigma))
        return base_s * max(1.0, factor)
