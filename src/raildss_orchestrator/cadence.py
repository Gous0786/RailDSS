"""Re-plan cadence and the freeze window (spec FR-OPT-07; OD-25, DR-0003).

* Re-plan immediately on a trigger event (``opt.replan_triggers``) and at least every
  ``opt.replan_max_interval_min`` minutes.
* Decisions that start within ``opt.freeze_window_min`` of the plan time are frozen: the next plan keeps
  their line and time. A decision that has begun is history and never changes either.
"""

from __future__ import annotations

from collections.abc import Iterable

from raildss_optimiser.greedy import Decision
from raildss_params import Register

SECONDS_PER_MIN = 60


class Cadence:
    def __init__(self, register: Register) -> None:
        self.max_interval_s = float(register.value("opt.replan_max_interval_min")) * SECONDS_PER_MIN
        self.freeze_s = float(register.value("opt.freeze_window_min")) * SECONDS_PER_MIN
        self.triggers = frozenset(str(t) for t in register.value("opt.replan_triggers"))
        self.last_plan_s: float | None = None
        self.first_plan_s: float | None = None
        self.frozen: dict[tuple[str, str], Decision] = {}
        self.done: dict[tuple[str, str], Decision] = {}  # started decisions: history never changes
        self.starts: list[float] = []

    def fired(self, events: Iterable[str]) -> list[str]:
        """The events that are replan triggers."""
        return sorted({e for e in events if e in self.triggers})

    def due(self, now_s: float, events: Iterable[str] = ()) -> bool:
        if self.last_plan_s is None:
            return True
        return bool(self.fired(events)) or now_s - self.last_plan_s >= self.max_interval_s

    def freeze(self, previous: Iterable[Decision], now_s: float) -> list[Decision]:
        """Freeze the previous plan's decisions inside the window; return every decision still to hold."""
        for d in previous:
            if now_s <= d.dep_s < now_s + self.freeze_s:
                self.frozen.setdefault((d.run_id, d.station), d)
        for k, d in self.frozen.items():
            if d.dep_s < now_s:
                self.done[k] = d
        self.frozen = {k: d for k, d in self.frozen.items() if d.dep_s >= now_s}
        return list(self.frozen.values())

    def history(self) -> list[Decision]:
        """Decisions that have started: re-planning re-simulates the day, so they stay fixed (VER-FRZ-01)."""
        return list(self.done.values())

    def mark(self, now_s: float) -> None:
        if self.first_plan_s is None:
            self.first_plan_s = now_s
        self.last_plan_s = now_s
        self.starts.append(now_s)

    @property
    def max_gap_s(self) -> float:
        return max((b - a for a, b in zip(self.starts, self.starts[1:], strict=False)), default=0.0)
