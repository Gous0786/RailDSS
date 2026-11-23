"""Alert load KPIs (spec §12.4 DSC-09; R02 H1, H2; NFR-USE-01, NFR-USE-02, FR-ALR-04; issue #163).

* **H1, alerts per controller-hour**: the alerts the DSS raised in the window, divided by the hours of the
  window and the controllers on duty (``alert.controllers_on_duty``). DSC-09: at most
  ``alert.steady_state_budget_per_h`` (6, EEMUA 191) on nominal scenarios.
* **H2, flood events**: more than ``alert.flood.alerts`` alerts in ``alert.flood.window_min`` minutes
  (FR-ALR-04: 10 alerts in 10 min). Each stretch of time over the threshold is one event, however many
  alerts it holds. DSC-09: none on nominal scenarios.

An **alert** is what interrupts the controller: a conflict that the detector (DS-5) newly reports with
priority P1 to P3 (P4 is informational, as in the loop), and a data-quality alert of the ingestor of the same
priorities. A conflict stays one alert while the detector keeps reporting it; one that goes away and comes
back is a new alert (as for D2). There is no alert manager (DS-14) yet: no grouping and no suppression, so
these are the raw alerts a controller would get, an upper bound for the managed alert load.
"""

from __future__ import annotations

from collections import Counter, deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from raildss_params import Register, load

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
COUNTED_PRIORITIES = ("P1", "P2", "P3")  # P4 is informational (the loop's open-conflict priorities)
Key = tuple[Any, ...]


class Reported(Protocol):
    """What the log reads of a conflict (``raildss_dss.conflicts.Conflict`` satisfies it)."""

    @property
    def priority(self) -> str: ...

    @property
    def key(self) -> Key: ...


@dataclass(frozen=True)
class AlertKpis:
    alerts: int
    hours: float
    controllers: int
    per_controller_hour: float | None  # None: an empty window
    budget_per_hour: float
    flood_events: int
    max_in_flood_window: int  # most alerts seen inside one flood window
    by_priority: dict[str, int]
    from_conflicts: int
    from_data_quality: int

    @property
    def within_budget(self) -> bool:
        return self.per_controller_hour is not None and self.per_controller_hour <= self.budget_per_hour

    def as_dict(self) -> dict[str, Any]:
        return {
            "alerts": self.alerts,
            "hours": self.hours,
            "controllers": self.controllers,
            "H1_alerts_per_controller_hour": self.per_controller_hour,
            "budget_per_hour": self.budget_per_hour,
            "within_budget": self.within_budget,
            "H2_flood_events": self.flood_events,
            "max_in_flood_window": self.max_in_flood_window,
            "by_priority": dict(self.by_priority),
            "from_conflicts": self.from_conflicts,
            "from_data_quality": self.from_data_quality,
        }


class AlertLog:
    """The alerts of a run, tick by tick (times in seconds on the scenario clock)."""

    def __init__(self, register: Register | None = None) -> None:
        reg = register or load()
        flood = reg.value("alert.flood")
        self.flood_alerts = int(flood["alerts"])
        self.flood_window_s = float(flood["window_min"]) * SECONDS_PER_MINUTE
        self.budget = float(reg.value("alert.steady_state_budget_per_h"))
        self.controllers = int(reg.value("alert.controllers_on_duty"))
        self.times: list[float] = []
        self.priorities: list[str] = []
        self.sources: list[str] = []  # "conflict" | "data_quality"
        self._previous: set[Key] = set()

    def record(
        self, now_s: float, conflicts: Iterable[Reported], data_quality: Iterable[Mapping[str, Any]] = ()
    ) -> None:
        current: set[Key] = set()
        for c in conflicts:
            if c.priority not in COUNTED_PRIORITIES:
                continue
            current.add(c.key)
            if c.key not in self._previous:  # newly reported, or back after it had gone
                self._add(now_s, c.priority, "conflict")
        self._previous = current
        for a in data_quality:
            if a.get("priority") in COUNTED_PRIORITIES:
                self._add(now_s, str(a["priority"]), "data_quality")

    def _add(self, now_s: float, priority: str, source: str) -> None:
        self.times.append(now_s)
        self.priorities.append(priority)
        self.sources.append(source)

    def floods(self) -> tuple[int, int]:
        """(flood events, most alerts in one window); a window over ``alert.flood.alerts`` is a flood."""
        window: deque[float] = deque()
        flooding, events, peak = False, 0, 0
        for t in sorted(self.times):
            while window and window[0] <= t - self.flood_window_s:
                window.popleft()
            if len(window) <= self.flood_alerts:
                flooding = False  # the count had dropped to the threshold before this alert
            window.append(t)
            peak = max(peak, len(window))
            if len(window) > self.flood_alerts and not flooding:
                flooding, events = True, events + 1
        return events, peak

    def kpis(self, from_s: float, to_s: float) -> AlertKpis:
        """H1 and H2 of the alerts between ``from_s`` and ``to_s``."""
        hours = max(0.0, (to_s - from_s) / SECONDS_PER_HOUR)
        events, peak = self.floods()
        n = len(self.times)
        return AlertKpis(
            alerts=n,
            hours=round(hours, 2),
            controllers=self.controllers,
            per_controller_hour=round(n / (hours * self.controllers), 2) if hours > 0 else None,
            budget_per_hour=self.budget,
            flood_events=events,
            max_in_flood_window=peak,
            by_priority=dict(sorted(Counter(self.priorities).items())),
            from_conflicts=self.sources.count("conflict"),
            from_data_quality=self.sources.count("data_quality"),
        )
