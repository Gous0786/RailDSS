"""Observation model and degradation profiles (issue #52, spec FR-TWIN-13, §6.4, §7.1).

One real day (20 Aug 2025) is simulated once, observed under D0, D1, D3 and six D2 seeds at the same
time (observers only read the simulation). D2 rates are pooled over six seeds and measured from
the delivered feeds alone, against D0 (the same truth, observed cleanly) and the ``gt.*`` trace.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import itertools
import statistics
from collections import Counter
from collections.abc import Iterable
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope, EventType, parse_event
from raildss_common.topics import lint, load_catalogue
from raildss_twin import run
from raildss_twin.observe import FEEDS, TOPIC, Observer, load_profile
from raildss_twin.plans import day_start

DAY = dt.date(2025, 8, 20)
ORIGIN = day_start(DAY)
D2_SEEDS = (0, 1, 2, 3, 4, 5)
TOLERANCE = 0.2  # issue #52: measured D2 rates within +-20 % of the profile
Events = list[tuple[str, EventEnvelope]]


@pytest.fixture(scope="module")
def observed() -> dict[str, Any]:
    world = run.load_world()
    obs = {
        "D0": Observer(world.register, "D0", keep=True),
        "D1": Observer(world.register, "D1", keep=True),
        "D3": Observer(world.register, "D3", keep=True),
    }
    d2 = [Observer(world.register, "D2", seed=s, keep=True) for s in D2_SEEDS]
    report, sim = run.run_day(world, DAY, keep_events=True, observers=[*obs.values(), *d2])
    plain, _ = run.run_day(world, DAY)
    return {"report": report, "sim": sim, "plain": plain, "d2": d2, **obs}


def topic(events: Events, name: str) -> list[EventEnvelope]:
    return [e for t, e in events if t == name]


def seconds(t: dt.datetime | None) -> float:
    assert t is not None
    return (t - ORIGIN).total_seconds()


def first_copies(events: Iterable[EventEnvelope]) -> list[EventEnvelope]:
    seen: set[Any] = set()
    out = []
    for e in events:
        if e.event_id not in seen:
            seen.add(e.event_id)
            out.append(e)
    return out


def differences(a: Counter[Any], b: Counter[Any], show: int = 5) -> list[Any]:
    """A few entries that differ (a small message: pytest's diff of two large counters is very slow)."""
    return [*list((a - b).items())[:show], *list((b - a).items())[:show]]


def within(measured: float, expected: float) -> bool:
    return abs(measured - expected) <= TOLERANCE * expected


# --------------------------------------------------------------------------- D0: exact reconstruction
def occupancy_from_obs(events: Events) -> Counter[tuple[dt.datetime, str, bool, str | None]]:
    out: Counter[tuple[dt.datetime, str, bool, str | None]] = Counter()
    for t, e in events:
        if t == "obs.block":
            label = f"BL:{e.payload['block_section'].replace('-', '~')}/{e.payload['line']}"
            out[(e.event_time, label, e.event_type == EventType.LINE_CLEAR, e.train_run_id)] += 1
        elif t == "obs.occupancy":
            out[(e.event_time, e.payload["tds_id"], e.payload["occupied"], None)] += 1
    return out


def test_d0_reconstructs_the_ground_truth_occupancy_exactly(observed: dict[str, Any]) -> None:
    truth: Counter[tuple[dt.datetime, str, bool, str | None]] = Counter()
    for e in observed["sim"].events:
        if e.event_type == EventType.TDS_OCCUPANCY:
            label = e.payload["tds_id"]
            train = e.train_run_id if label.startswith("BL:") else None  # a track circuit has no train id
            truth[(e.event_time, label, e.payload["occupied"], train)] += 1
    assert truth and observed["report"].completed
    assert differences(occupancy_from_obs(observed["D0"].events), truth) == []
    # replayed in delivery order, no track section is ever freed before it was occupied
    state: Counter[str] = Counter()
    for t, e in observed["D0"].events:
        if t in ("obs.block", "obs.occupancy"):
            occ = e.event_type == EventType.LINE_CLEAR if t == "obs.block" else e.payload["occupied"]
            section = e.payload.get("block_section", "").replace("-", "~")
            label = e.payload.get("tds_id") or f"BL:{section}/{e.payload['line']}"
            state[label] += 1 if occ else -1
            assert state[label] >= 0, (label, e.event_time)


def test_d0_station_events_match_every_route_set(observed: dict[str, Any]) -> None:
    truth = Counter(
        (
            e.train_run_id,
            e.payload["station"],
            "ARR" if e.payload["mode"] == "receive" else "DEP",
            e.event_time.replace(microsecond=0),
        )
        for e in observed["sim"].events
        if e.event_type == EventType.ROUTE_SET
    )
    seen = Counter(
        (e.train_run_id, e.payload["station"], e.payload["kind"], e.event_time)
        for e in topic(observed["D0"].events, "obs.station")
    )
    assert differences(seen, truth) == []


def test_d0_delivers_on_time_in_order_and_every_train_reports(observed: dict[str, Any]) -> None:
    events = [e for _, e in observed["D0"].events]
    ingest = [seconds(e.ingest_time) for e in events]
    assert ingest == sorted(ingest)
    assert all(seconds(e.ingest_time) - seconds(e.event_time) < 1 for e in events)
    assert len({e.event_id for e in events}) == len(events)
    entered = {e.train_run_id for e in observed["sim"].events if e.event_type == EventType.TRAIN_STATE}
    positioned = {e.train_run_id for e in topic(observed["D0"].events, "obs.position")}
    assert positioned == entered


def test_rtis_fixes_are_thirty_seconds_apart(observed: dict[str, Any]) -> None:
    interval = observed["D0"].interval_s
    by_train: dict[str | None, list[float]] = {}
    for e in topic(observed["D0"].events, "obs.position"):
        by_train.setdefault(e.train_run_id, []).append(seconds(e.event_time))
    gaps = {b - a for times in by_train.values() for a, b in itertools.pairwise(times)}
    assert gaps == {interval}


def test_every_observed_event_is_a_valid_envelope_on_a_dss_readable_topic(observed: dict[str, Any]) -> None:
    catalogue = load_catalogue()
    assert lint(catalogue) == []  # `raildss_common kafka lint` with the new producers
    twin = catalogue.principal("twin")
    for name in observed["D0"].counts:
        t = catalogue.topic(name)
        assert not t.is_ground_truth
        assert any(g.matches(name) for g in twin.write), f"twin may not write {name}"
        assert catalogue.can_read("dss", name)
    for _, e in observed["D0"].events[:: max(1, len(observed["D0"].events) // 5000)]:
        assert parse_event(e.to_json()) == e
    for o in observed["d2"]:
        for e in topic(o.events, "obs.station"):
            assert parse_event(e.to_json()) == e


def test_observers_do_not_change_the_ground_truth(observed: dict[str, Any]) -> None:
    assert observed["report"].trace_hash == observed["plain"].trace_hash


def test_heartbeats_cover_every_feed(observed: dict[str, Any]) -> None:
    beats = topic(observed["D0"].events, "obs.heartbeat")
    assert {e.payload["source"] for e in beats} == set(FEEDS)
    counted = Counter()
    for e in beats:
        counted[e.payload["source"]] += e.payload["count_10s"]
    delivered = observed["D0"].counts
    for source in FEEDS:  # the last partial window after the final heartbeat is not counted
        assert 0 <= delivered[TOPIC[source]] - counted[source] <= delivered[TOPIC[source]] * 0.01


# --------------------------------------------------------------------------- D1 and D3
def test_d1_station_events_are_minute_resolution(observed: dict[str, Any]) -> None:
    resolution = observed["D1"].profile.station_resolution_s
    station = topic(observed["D1"].events, "obs.station")
    assert station and all(seconds(e.event_time) % resolution == 0 for e in station)
    assert all(seconds(e.ingest_time) >= seconds(e.event_time) for e in station)


def test_d3_has_one_full_outage_of_15_to_30_min(observed: dict[str, Any]) -> None:
    o = observed["D3"]
    low, high = o.profile.outage_s
    assert len(o.outages) == 1
    a, b = o.outages[0]
    assert low <= b - a <= high
    silent = [
        e for t, e in o.events if t in ("obs.position", "obs.heartbeat") and a <= seconds(e.event_time) < b
    ]
    assert silent == []
    held = [e for t, e in o.events if t == "obs.block" and a <= seconds(e.event_time) < b]
    assert held and all(seconds(e.ingest_time) >= b for e in held)  # store and forward


def test_held_station_events_are_not_delivered_during_an_outage() -> None:
    """Every entry is late, so held entries are released by the next entry, also inside outages."""
    world = run.load_world()
    hour = 3600.0
    profile = dataclasses.replace(
        load_profile(world.register, "D1"),
        name="LATE",
        out_of_order=1.0,
        outage_every_s=hour,
        outage_s=(hour / 3, hour / 3),
    )
    o = Observer(world.register, profile, keep=True)
    run.run_day(world, DAY, until_s=4 * hour, observers=[o])
    station = topic(o.events, "obs.station")
    assert o.outages and station
    during = [e for e in station if o.in_outage(seconds(e.ingest_time))]
    assert during == []


def test_unknown_profile_is_rejected() -> None:
    with pytest.raises(ValueError, match="D0"):
        load_profile(run.load_world().register, "D9")


# --------------------------------------------------------------------------- D2: measured rates
def test_d2_rtis_coverage_and_dropout_match_the_profile(observed: dict[str, Any]) -> None:
    clean = topic(observed["D0"].events, "obs.position")
    trains = {e.train_run_id for e in clean}
    covered = delivered = expected = 0
    for o in observed["d2"]:
        got = topic(o.events, "obs.position")
        mine = {e.train_run_id for e in got}
        covered += len(mine)
        expected += sum(1 for e in clean if e.train_run_id in mine and not o.in_outage(seconds(e.event_time)))
        delivered += len(got)
    profile = observed["d2"][0].profile
    coverage = covered / (len(trains) * len(D2_SEEDS))
    dropout = 1 - delivered / expected
    assert within(coverage, profile.rtis_coverage), coverage
    assert within(dropout, profile.position_dropout), dropout


def test_d2_station_event_rates_match_the_profile(observed: dict[str, Any]) -> None:
    truth = {
        (e.train_run_id, e.payload["station"], "ARR" if e.payload["mode"] == "receive" else "DEP"): seconds(
            e.event_time
        )
        for e in observed["sim"].events
        if e.event_type == EventType.ROUTE_SET
    }
    first_station: dict[str | None, str] = {}
    for e in topic(observed["D0"].events, "obs.station"):
        first_station.setdefault(e.train_run_id, e.payload["station"])
    mid_dep = sum(
        1
        for e in topic(observed["D0"].events, "obs.station")
        if e.payload["kind"] == "DEP" and e.payload["station"] != first_station[e.train_run_id]
    )
    missing = duplicates = unique = late = followed = 0
    latencies: list[float] = []
    for o in observed["d2"]:
        station = topic(o.events, "obs.station")
        firsts = first_copies(station)
        unique += len(firsts)
        followed += len(firsts) - len({e.train_run_id for e in firsts})  # a last event cannot be late
        duplicates += len(station) - len(firsts)
        missing += mid_dep - sum(
            1
            for e in firsts
            if e.payload["kind"] == "DEP" and e.payload["station"] != first_station[e.train_run_id]
        )
        top: dict[str | None, int] = {}
        for e in firsts:
            if e.seq < top.get(e.train_run_id, 0):
                late += 1
                continue
            top[e.train_run_id] = e.seq
            true_s = truth[(e.train_run_id, e.payload["station"], e.payload["kind"])]
            if not any(a <= seconds(e.ingest_time) <= b for a, b in o.outages):
                latencies.append(seconds(e.ingest_time) - true_s)
    p = observed["d2"][0].profile
    rates = {
        "missing departures": (missing / (mid_dep * len(D2_SEEDS)), p.missing_departure),
        "duplicates": (duplicates / unique, p.duplicates),
        "out of order": (late / followed, p.out_of_order),
        "mean latency": (statistics.fmean(latencies), p.latency.mean_s),
    }
    off = {k: v for k, v in rates.items() if not within(*v)}
    assert off == {}, rates
    assert min(latencies) >= p.latency.low_s
