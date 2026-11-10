"""DS-3 state estimation and snapshots (issue #71, spec FR-PRED-01, FR-PRED-02).

A real day (20 Aug 2025) is simulated once and observed under D0. The released events of DS-1 drive the
estimator, which is compared with the ground truth (``gt.state``, every 60 s) for every train at every
snapshot. Only the tests import the twin.
"""

from __future__ import annotations

import datetime as dt
import itertools
import json
from collections import defaultdict
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope, EventType
from raildss_dss.ingest import Ingestor, MemoryTransport
from raildss_dss.state import PlanDirectory, StateEstimator
from raildss_twin import run
from raildss_twin.observe import Observer
from tests.common.events import valid_fixtures

DAY = dt.date(2025, 8, 20)
KM_TOL = 0.0011  # gt.state rounds km to 3 decimals
SPEED_TOL = 0.051  # and speed to 1 decimal


@pytest.fixture(scope="module")
def day() -> dict[str, Any]:
    world = run.load_world()
    obs = Observer(world.register, "D0", keep=True)
    _, sim = run.run_day(world, DAY, keep_events=True, observers=[obs])
    ing = Ingestor(world.register)
    ing.run(MemoryTransport([(t, e.to_json()) for t, e in obs.events]))
    truth: dict[dt.datetime, list[EventEnvelope]] = defaultdict(list)
    for e in sim.events:
        if e.event_type == EventType.TRAIN_STATE:
            truth[e.event_time].append(e)
    return {
        "events": sorted(ing.released, key=lambda e: e.event_time),
        "truth": dict(sorted(truth.items())),
        "directory": PlanDirectory.from_published(register=world.register),
        "register": world.register,
    }


def replay(day: dict[str, Any]) -> tuple[StateEstimator, list[Any]]:
    """Snapshots at every ground-truth time, from the events up to that time."""
    est = StateEstimator(day["directory"], day["register"])
    events, i, snaps = day["events"], 0, []
    for t in day["truth"]:
        while i < len(events) and events[i].event_time <= t:
            est.apply(events[i])
            i += 1
        snaps.append(est.snapshot(t))
    return est, snaps


def test_d0_estimated_state_matches_ground_truth_for_every_train_at_every_snapshot(
    day: dict[str, Any],
) -> None:
    est, snaps = replay(day)
    # A station event at exactly the sample instant, or a train finishing at it, has no defined order
    # relative to the sample: those (train, instant) pairs are skipped and counted.
    at_instant = {
        (e.train_run_id, e.event_time) for e in day["events"] if e.event_type == EventType.STATION_EVENT
    }
    final = {}
    for t, states in day["truth"].items():
        for g in states:
            final[g.train_run_id] = t
    compared, skipped, wrong = 0, 0, []
    for snap, (t, states) in zip(snaps, day["truth"].items(), strict=True):
        seen = set()
        for g in states:
            seen.add(g.train_run_id)
            st = snap.train(g.train_run_id)
            if (g.train_run_id, t) in at_instant or (st is None and final[g.train_run_id] == t):
                skipped += 1
                continue
            compared += 1
            assert g.location is not None
            ok = (
                st is not None
                and st.resource == g.location.ne_id
                and st.km is not None
                and abs(st.km - float(g.location.km or 0)) <= KM_TOL
                and st.speed_kmh is not None
                and abs(st.speed_kmh - float(g.payload["speed_kmh"])) <= SPEED_TOL
            )
            if not ok:
                wrong.append((t, g.train_run_id, g.location.ne_id, st))
        # a train the truth no longer has is one the DSS has just lost or reports as stale (unknown)
        for st in snap.trains:
            if st.run_id not in seen:
                assert st.stale or (st.last_fix_age_s or 0) <= est.stale_s, (t, st)
    assert wrong == [], wrong[:5]
    assert compared > 20_000 and skipped / (compared + skipped) < 0.03


def test_snapshots_are_reproducible_and_versioned_on_material_change(day: dict[str, Any]) -> None:
    _, a = replay(day)
    _, b = replay(day)
    assert [s.hash for s in a] == [s.hash for s in b]
    assert all(s.network_version == day["directory"].network_version for s in a)
    versions = [s.version for s in a]
    assert versions == sorted(versions) and versions[-1] > 1
    for prev, cur in itertools.pairwise(a):
        material = lambda s: [(x.run_id, x.resource, x.stale) for x in s.trains]  # noqa: E731
        assert (cur.version > prev.version) == (material(cur) != material(prev))
    json.dumps(a[len(a) // 2].to_json())  # serialisable


def test_a_stale_train_is_unknown_and_never_extrapolated(day: dict[str, Any]) -> None:
    _, _, fix = valid_fixtures()[0]
    est = StateEstimator(day["directory"], day["register"])
    from raildss_common.envelope import parse_event

    est.apply(parse_event(json.dumps(fix)))
    t0 = dt.datetime.fromisoformat(fix["event_time"])
    run_id = fix["train_run_id"]
    at = {s: est.snapshot(t0 + dt.timedelta(seconds=s)).train(run_id) for s in (60, 120, 121)}
    assert at[60] is not None and at[60].km == fix["location"]["km"]  # moving at 74 km/h, not advanced
    assert at[120] is not None and not at[120].stale and at[120].position_known
    assert at[121] is not None and at[121].stale and at[121].km is None and not at[121].position_known
    assert at[121].last_fix_age_s == 121


def test_late_events_do_not_change_the_current_state(day: dict[str, Any]) -> None:
    _, _, fix = valid_fixtures()[0]
    from raildss_common.envelope import parse_event

    est = StateEstimator(day["directory"], day["register"])
    late = parse_event(
        json.dumps(dict(fix, quality={"simulated_noise": True, "late": True, "duplicate_of": None}))
    )
    est.apply(late)
    assert est.trains == {}


def test_passenger_delay_against_the_timetable(day: dict[str, Any]) -> None:
    est, _ = replay(day)
    passenger = [tr for tr in est.trains.values() if "@" in tr.run_id and tr.delay_s is not None]
    assert passenger, "no passenger delay computed"
    assert all(abs(tr.delay_s) < 6 * 3600 for tr in passenger)  # booked times resolved to the right day
