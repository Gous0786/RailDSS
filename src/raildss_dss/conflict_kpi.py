"""Conflict KPIs D1 and D2 (spec §12.4 DSC-04, DSC-05; R02 D1, D2; issue #162).

* **D1, median conflict lead time**: for every conflict the DS-5 detector reported, the time from its first
  detection to the occurrence it predicted at that moment. DSC-05: median at least 15 min.
* **D2, conflicts resolved before occurrence**: the share of the conflicts that are over (no longer
  predicted) before their predicted time, among those that are over or came due. DSC-04: at least 90 %.

:class:`ConflictLog` follows the detector's output tick by tick: a conflict is tracked by its key (type,
trains, resource), from the first tick it is reported to the last. It ends one of three ways:

* **resolved**: not reported any more at a tick before its (last) predicted time. The DSS log alone cannot
  tell a real resolution from a prediction that changed its mind, so the **ground truth** decides: the
  conflict only counts as resolved if no train of it was held (``waiting``) at one of the resource's
  stations within ``kpi.conflict_match_window_min`` of the predicted time (:func:`gt_occurrence`);
* **occurred**: still reported when its predicted time came, or resolved on paper but held in the ground
  truth;
* **open**: still reported at the end of the run with a predicted time after it. Open conflicts are left out
  of D2 (they are neither) and counted, so a short window does not inflate the share.

Hand-made logs test the rules (``tests/dss/test_conflict_kpi.py``); the replay and scenario reports show
both KPIs.
"""

from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from raildss_common.envelope import EventType
from raildss_params import Register, load

SECONDS_PER_MINUTE = 60
Key = tuple[Any, ...]


class Reported(Protocol):
    """What the log reads of a conflict (``raildss_dss.conflicts.Conflict`` satisfies it)."""

    @property
    def type(self) -> int: ...

    @property
    def trains(self) -> tuple[str, ...]: ...

    @property
    def resource(self) -> str: ...

    @property
    def time(self) -> dt.datetime: ...  # predicted start of the conflict

    @property
    def near(self) -> bool: ...

    @property
    def key(self) -> Key: ...


@dataclass
class Track:
    """One conflict over the run, in seconds on the scenario clock."""

    key: Key
    type: int
    trains: tuple[str, ...]
    resource: str
    near: bool
    first_s: float
    lead_s: float  # predicted occurrence minus first detection, at first detection
    last_s: float  # last tick it was reported
    predicted_s: float  # predicted occurrence at the last tick it was reported
    ended_s: float | None = None  # first tick it was not reported any more


@dataclass(frozen=True)
class ConflictKpis:
    conflicts: int
    d1_median_lead_min: float | None  # None: no conflict
    resolved: int
    occurred: int
    open: int
    d2_resolved_share_pct: float | None  # None: nothing resolved or occurred yet
    near: int

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "conflicts": self.conflicts,
            "D1_median_conflict_lead_min": self.d1_median_lead_min,
            "D2_resolved_before_occurrence_pct": self.d2_resolved_share_pct,
            "resolved": self.resolved,
            "occurred": self.occurred,
            "open": self.open,
            "near": self.near,
        }


class ConflictLog:
    """The detector's conflicts, tick by tick (DS-5 log); ``origin`` is the clock's zero.

    A conflict that is reported, goes away and comes back later is two conflicts: the first ended."""

    def __init__(self, origin: dt.datetime) -> None:
        self.origin = origin
        self.tracks: list[Track] = []
        self._live: dict[Key, Track] = {}
        self.ticks = 0
        self.now_s = 0.0

    def record(self, now_s: float, conflicts: Iterable[Reported]) -> None:
        self.ticks += 1
        self.now_s = now_s
        seen: set[Key] = set()
        for c in conflicts:
            predicted = (c.time - self.origin).total_seconds()
            seen.add(c.key)
            track = self._live.get(c.key)
            if track is None:
                track = Track(
                    c.key, c.type, c.trains, c.resource, c.near, now_s, predicted - now_s, now_s, predicted
                )
                self.tracks.append(track)
                self._live[c.key] = track
            else:
                track.last_s, track.predicted_s = now_s, predicted
                track.near = track.near and c.near  # near only while it has never been a firm prediction
        for key in [k for k in self._live if k not in seen]:
            self._live.pop(key).ended_s = now_s

    def kpis(
        self,
        occurred: Callable[[Track], bool] | None = None,
        *,
        to_s: float | None = None,
    ) -> ConflictKpis:
        """D1 and D2 of the run so far. ``occurred`` is the ground-truth check (None: log only)."""
        end = self.now_s if to_s is None else to_s
        resolved = occ = open_ = 0
        for t in self.tracks:
            if t.ended_s is not None and t.ended_s <= t.predicted_s:  # gone before it was due
                if occurred is not None and occurred(t):
                    occ += 1
                else:
                    resolved += 1
            elif t.ended_s is None and t.predicted_s > end:  # still predicted for later
                open_ += 1
            else:  # came due while reported, or ended after its time
                occ += 1
        leads = [max(0.0, t.lead_s) for t in self.tracks]  # already in progress: lead 0
        decided = resolved + occ
        return ConflictKpis(
            conflicts=len(self.tracks),
            d1_median_lead_min=round(statistics.median(leads) / SECONDS_PER_MINUTE, 1) if leads else None,
            resolved=resolved,
            occurred=occ,
            open=open_,
            d2_resolved_share_pct=round(100 * resolved / decided, 2) if decided else None,
            near=sum(t.near for t in self.tracks),
        )


def gt_occurrence(
    events: Sequence[Any], origin: dt.datetime, register: Register | None = None
) -> Callable[[Track], bool]:
    """Ground-truth check: a train of the conflict stood held (``waiting``) at a station of its resource
    within ``kpi.conflict_match_window_min`` of the predicted time. ``events`` are the ``gt.*`` events of
    the run (``Sim.events``)."""
    reg = register or load()
    window_s = float(reg.value("kpi.conflict_match_window_min")) * SECONDS_PER_MINUTE
    held: dict[str, list[tuple[float, str]]] = defaultdict(list)
    for e in events:
        if e.event_type is EventType.TRAIN_STATE and e.train_run_id and e.payload.get("status") == "waiting":
            held[e.train_run_id].append(
                ((e.event_time - origin).total_seconds(), str(e.payload.get("station")))
            )

    def occurred(t: Track) -> bool:
        stations = set(t.resource.split("~"))
        return any(
            station in stations and abs(when - t.predicted_s) <= window_s
            for train in t.trains
            for when, station in held.get(train, ())
        )

    return occurred
