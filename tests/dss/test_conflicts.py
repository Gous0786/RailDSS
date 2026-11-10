"""DS-5 conflict detection (issue #73, spec FR-CD-01..07).

Seeded scenarios on a small synthetic network give at least three known conflicts per type (1-4, 6, 7);
each must be found with the right type and trains (100 % recall). One test runs the full chain (twin
window -> DS-1 -> DS-3 -> DS-4 -> DS-5) for the latency target.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import pytest

from raildss_dss.conflicts import (
    CROSSING,
    DEADLOCK,
    FOLLOWING,
    HEAD_ON,
    PLATFORM,
    RULE,
    CrewDuty,
    DetectionSchedule,
    Detector,
    Line,
    MaintenanceWindow,
    Occupation,
    ResourceModel,
    StabilityTracker,
    StationLine,
    stability_report,
)
from raildss_dss.state import PlanDirectory
from raildss_params import load

T0 = dt.datetime(2025, 8, 20, 6, 0, tzinfo=dt.UTC)
SINGLE, DOUBLE, AUTO = "A~B", "B~C", "C~D"


def at(minutes: float) -> dt.datetime:
    return T0 + dt.timedelta(minutes=minutes)


def occ(
    run: str, res: str, start: float, end: float, *, kind: str = "block", way: str = "DN", **kw: Any
) -> Occupation:
    s90, e90 = kw.pop("p90", (start, end))
    return Occupation(run, res, kind, at(start), at(end), at(s90), at(e90), direction=way, **kw)


def stand(run: str, station: str, start: float, end: float, **kw: Any) -> Occupation:
    return occ(run, station, start, end, kind="station", way="", stop=True, **kw)


@pytest.fixture(scope="module")
def detector() -> Detector:
    absolute = lambda i, d: Line(i, d, True)  # noqa: E731
    resources = ResourceModel(
        lines={
            SINGLE: [absolute("S", "BOTH")],
            DOUBLE: [absolute("DN", "DN"), absolute("UP", "UP")],
            AUTO: [Line("DN", "DN", False), Line("MID", "BOTH", False), Line("UP", "UP", False)],
        },
        stations={
            "A": [StationLine(1, True, True, 700.0), StationLine(2, True, True, 700.0)],
            "B": [StationLine(1, True, True, 700.0), StationLine(2, False, False, 700.0)],
            "C": [
                StationLine(1, False, False, 500.0),
                StationLine(2, False, False, 650.0),
                StationLine(3, True, True, 750.0),
            ],
            "D": [StationLine(1, True, True, 700.0), StationLine(2, False, False, 700.0)],
        },
    )
    directory = PlanDirectory("TEST", {"A": 0.0, "B": 10.0, "C": 20.0, "D": 30.0}, set(), {})
    return Detector.build(directory, load(), resources)


def found(conflicts: list[Any], ctype: int, trains: tuple[str, ...]) -> Any:
    hit = [c for c in conflicts if c.type == ctype and c.trains == tuple(sorted(trains))]
    assert hit, f"type {ctype} {trains} not found in {[(c.type, c.trains) for c in conflicts]}"
    return hit[0]


# --------------------------------------------------------------------------- seeded scenarios
CLOSE_UP = [MaintenanceWindow(DOUBLE, "UP", at(0), at(60))]
CLOSE_DN_UP_AUTO = [
    MaintenanceWindow(AUTO, "DN", at(0), at(60)),
    MaintenanceWindow(AUTO, "UP", at(0), at(60)),
]

SCENARIOS: list[
    tuple[str, int, tuple[str, ...], list[Occupation], list[MaintenanceWindow], list[CrewDuty]]
] = [
    # (1) head-on on single line
    (
        "head-on single line",
        HEAD_ON,
        ("X", "Y"),
        [occ("X", SINGLE, 10, 20), occ("Y", SINGLE, 15, 25, way="UP")],
        [],
        [],
    ),
    (
        "head-on single-line working",
        HEAD_ON,
        ("X", "Y"),
        [occ("X", DOUBLE, 10, 20), occ("Y", DOUBLE, 12, 18, way="UP")],
        CLOSE_UP,
        [],
    ),
    (
        "head-on on the reversible line",
        HEAD_ON,
        ("X", "Y"),
        [occ("X", AUTO, 30, 40), occ("Y", AUTO, 35, 45, way="UP")],
        CLOSE_DN_UP_AUTO,
        [],
    ),
    # (2) following / headway / absolute block
    (
        "following absolute block",
        FOLLOWING,
        ("X", "Y"),
        [occ("X", DOUBLE, 10, 20), occ("Y", DOUBLE, 15, 25)],
        [],
        [],
    ),
    (
        "following single line",
        FOLLOWING,
        ("X", "Y"),
        [occ("X", SINGLE, 10, 20), occ("Y", SINGLE, 19, 30)],
        [],
        [],
    ),
    (
        "headway automatic block",
        FOLLOWING,
        ("X", "Y"),
        [occ("X", AUTO, 10, 20), occ("Y", AUTO, 11, 21)],
        [],
        [],
    ),
    # (3) crossing infeasibility
    (
        "crossing, station full",
        CROSSING,
        ("X", "Y"),
        [stand("X", "B", 10, 30), stand("Y", "B", 12, 30), stand("Z", "B", 14, 30)],
        [],
        [],
    ),
    (
        "crossing at A",
        CROSSING,
        ("P", "Q"),
        [stand("P", "A", 0, 20), stand("Q", "A", 5, 20), stand("R", "A", 10, 20)],
        [],
        [],
    ),
    (
        "crossing, CSR too short",
        CROSSING,
        ("L1", "L2"),
        [stand("L1", "C", 10, 30, length_m=700.0), stand("L2", "C", 15, 30, length_m=700.0)],
        [],
        [],
    ),
    # (4) platform
    (
        "platform, one platform line",
        PLATFORM,
        ("P1", "P2"),
        [stand("P1", "B", 10, 15, passenger=True), stand("P2", "B", 12, 16, passenger=True)],
        [],
        [],
    ),
    (
        "platform, same booked platform",
        PLATFORM,
        ("P1", "P2"),
        [
            stand("P1", "A", 10, 15, passenger=True, platform="1"),
            stand("P2", "A", 12, 16, passenger=True, platform="1"),
        ],
        [],
        [],
    ),
    (
        "platform at D",
        PLATFORM,
        ("P3", "P4"),
        [stand("P3", "D", 40, 45, passenger=True), stand("P4", "D", 42, 50, passenger=True)],
        [],
        [],
    ),
    # (6) deadlock risk: X waits at full A for the block Y holds; Y waits for A
    (
        "deadlock via head-on",
        DEADLOCK,
        ("X", "Y"),
        [
            stand("X", "A", 0, 10, seq=0),
            occ("X", SINGLE, 10, 20, seq=1),
            stand("Z", "A", 0, 60),
            occ("Y", SINGLE, 5, 15, way="UP", seq=0),
            stand("Y", "A", 15, 30, seq=1),
        ],
        [],
        [],
    ),
    (
        "deadlock under single-line working",
        DEADLOCK,
        ("X", "Y"),
        [
            stand("X", "B", 0, 10, seq=0),
            occ("X", DOUBLE, 10, 20, seq=1),
            stand("Z", "B", 0, 60),
            occ("Y", DOUBLE, 5, 15, way="UP", seq=0),
            stand("Y", "B", 15, 30, seq=1),
        ],
        CLOSE_UP,
        [],
    ),
    (
        "deadlock via crossing",
        DEADLOCK,
        ("X", "Y"),
        [
            stand("X", "A", 0, 25, seq=0),
            occ("X", SINGLE, 25, 35, seq=1),
            stand("Z", "A", 0, 60),
            occ("Y", SINGLE, 5, 15, way="UP", seq=0),
            stand("Y", "A", 15, 30, seq=1),
        ],
        [],
        [],
    ),
    # (7) rules
    (
        "segment closed for maintenance",
        RULE,
        ("X",),
        [occ("X", DOUBLE, 10, 20)],
        [MaintenanceWindow(DOUBLE, None, at(0), at(30))],
        [],
    ),
    (
        "own line closed, none usable",
        RULE,
        ("X",),
        [occ("X", DOUBLE, 10, 20)],
        [MaintenanceWindow(DOUBLE, "DN", at(0), at(30))],
        [],
    ),
    (
        "crew duty expires en route",
        RULE,
        ("X",),
        [occ("X", DOUBLE, 10, 20), stand("X", "C", 20, 40, seq=1)],
        [],
        [CrewDuty("X", at(-540), dt.timedelta(hours=9, minutes=15))],
    ),
]


@pytest.mark.parametrize("name,ctype,trains,occs,maint,crew", SCENARIOS, ids=[s[0] for s in SCENARIOS])
def test_seeded_conflict_is_found_with_its_type_and_trains(
    detector: Detector,
    name: str,
    ctype: int,
    trains: tuple[str, ...],
    occs: list[Occupation],
    maint: Any,
    crew: Any,
) -> None:
    conflicts = detector.detect(occs, T0, maint, crew)
    c = found(conflicts, ctype, trains)
    # FR-CD-05: every record carries type, trains, resource, time and lead time
    for r in conflicts:
        assert r.type and r.trains and r.resource and r.time >= T0 and r.lead_time_s >= 0
        assert r.priority in ("P1", "P2", "P3", "P4")
    assert c.lead_time_s == (c.time - T0).total_seconds()


def test_every_type_has_at_least_three_seeded_cases() -> None:
    counts = {
        t: sum(s[1] == t for s in SCENARIOS) for t in (HEAD_ON, FOLLOWING, CROSSING, PLATFORM, DEADLOCK, RULE)
    }
    assert all(n >= 3 for n in counts.values()), counts


def test_no_conflict_when_lines_keep_trains_apart(detector: Detector) -> None:
    occs = [
        occ("X", DOUBLE, 10, 20),
        occ("Y", DOUBLE, 12, 18, way="UP"),
        occ("U", AUTO, 10, 20),
        occ("V", AUTO, 15, 25),
    ]
    occs += [stand("P", "A", 10, 20), stand("Q", "A", 12, 20)]
    assert detector.detect(occs, T0) == []


def test_head_on_with_both_trains_committed_has_no_feasible_resolution(detector: Detector) -> None:
    occs = [occ("X", SINGLE, 0, 20, committed=True), occ("Y", SINGLE, 0, 25, way="UP", committed=True)]
    c = found(detector.detect(occs, T0), HEAD_ON, ("X", "Y"))
    assert c.status == "NO_FEASIBLE_RESOLUTION" and c.priority == "P2"


def test_near_conflict_band_from_p90(detector: Detector) -> None:
    occs = [occ("X", DOUBLE, 30, 40, p90=(30, 50)), occ("Y", DOUBLE, 45, 55, p90=(45, 60))]
    c = found(detector.detect(occs, T0), FOLLOWING, ("X", "Y"))
    assert c.near and c.priority == "P3"
    soon = [occ("X", DOUBLE, 5, 10, p90=(5, 15)), occ("Y", DOUBLE, 12, 20, p90=(12, 25))]
    c = found(detector.detect(soon, T0), FOLLOWING, ("X", "Y"))
    assert c.near and c.priority == "P2"  # lead < cd.urgent_lead_min keeps the base priority


# --------------------------------------------------------------------------- FR-CD-04 / FR-CD-07
def test_detection_runs_on_events_and_at_least_every_30_s() -> None:
    sched = DetectionSchedule.from_register(load())
    now = T0
    for step in range(600):  # ten simulated minutes in 1 s ticks, a state event every 47 s
        now = T0 + dt.timedelta(seconds=step)
        if sched.due(now, state_event=step % 47 == 0):
            sched.record(now)
    assert sched.max_gap_s <= sched.max_interval_s


def test_alert_stability_is_reported_per_scenario(detector: Detector) -> None:
    per: dict[str, dict[str, float]] = {}
    for name, _, _, occs, maint, crew in SCENARIOS[:6]:
        tracker = StabilityTracker()
        for k in range(10):  # every third run the second train's prediction moves 30 min later
            jitter = dt.timedelta(minutes=30 if k % 3 == 2 else 0)
            shifted = [
                o
                if i == 0
                else Occupation(
                    **{
                        **o.__dict__,
                        "start": o.start + jitter,
                        "end": o.end + jitter,
                        "start90": o.start90 + jitter,
                        "end90": o.end90 + jitter,
                    }
                )
                for i, o in enumerate(occs)
            ]
            tracker.update(at(k), detector.detect(shifted, at(k), maint, crew))
        per[name] = tracker.metrics()
    assert set(per) and all(m["runs"] == 10 and m["unique_conflicts"] >= 1 for m in per.values())
    assert any(m["flip_flops"] > 0 for m in per.values())
    table = stability_report(per)
    assert all(name in table for name in per)


# --------------------------------------------------------------------------- FR-CD-01 latency on real data
def test_full_chain_on_a_twin_window_runs_within_a_second() -> None:
    from raildss_dss.conflicts import occupations
    from raildss_dss.ingest import Ingestor, MemoryTransport
    from raildss_dss.predict import Predictor, ProcessTimes
    from raildss_dss.state import StateEstimator
    from raildss_twin import run
    from raildss_twin.observe import Observer
    from raildss_twin.plans import day_start

    world = run.load_world()
    reg = world.register
    obs = Observer(reg, "D0", keep=True)
    run.run_day(world, dt.date(2025, 8, 20), until_s=4 * 3600, observers=[obs])
    ing = Ingestor(reg)
    ing.run(MemoryTransport([(t, e.to_json()) for t, e in obs.events]))
    directory = PlanDirectory.from_published(register=reg)
    est = StateEstimator(directory, reg)
    now = day_start(dt.date(2025, 8, 20)) + dt.timedelta(hours=3)
    for e in sorted(ing.released, key=lambda e: e.event_time):
        if e.event_time <= now:
            est.apply(e)
    predictor = Predictor(ProcessTimes.from_log([], directory, reg), reg)
    det = Detector.build(directory, reg)
    t0 = time.perf_counter()
    preds = predictor.predict(est, now)
    occs = occupations(preds, est.snapshot(now), est, directory, predictor.horizon)
    conflicts = det.detect(occs, now)
    assert time.perf_counter() - t0 < 1.0 + 2.0  # detection < 1 s on top of the < 2 s prediction
    assert len({o.run_id for o in occs}) >= 5
    assert all(c.lead_time_s >= 0 for c in conflicts)


def test_opposing_traffic_on_double_line_is_not_a_head_on(detector: Detector) -> None:
    occs = [occ("X", DOUBLE, 10, 20), occ("Y", DOUBLE, 15, 25), occ("Z", DOUBLE, 12, 18, way="UP")]
    conflicts = detector.detect(occs, T0)
    found(conflicts, FOLLOWING, ("X", "Y"))
    assert not [c for c in conflicts if c.type == HEAD_ON]
