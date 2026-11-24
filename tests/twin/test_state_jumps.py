"""FV-01 (issue #144): a train reports one consistent position per state tick; no jumps back to a station."""

from __future__ import annotations

import datetime as dt

from raildss_common.envelope import EventType
from raildss_twin import run

DAY = dt.date(2025, 8, 20)
MAX_JUMP_KM = 3.0  # the face-validity threshold (docs/review/m1-face-validity.md)


def test_no_state_interval_jumps_more_than_3_km_on_a_real_day() -> None:
    world = run.load_world()
    _, sim = run.run_day(world, DAY, keep_events=True)
    period = float(world.register.value("twin.sim")["state_period_s"])
    last: dict[str, tuple[float, float]] = {}
    jumps = []
    intervals = 0
    for ev in sim.events:
        if ev.event_type is not EventType.TRAIN_STATE or ev.location is None or ev.train_run_id is None:
            continue
        t = ev.event_time.timestamp()
        prev = last.get(ev.train_run_id)
        if prev is not None and t - prev[0] <= period + 1:
            intervals += 1
            if abs(ev.location.km - prev[1]) > MAX_JUMP_KM:
                jumps.append((ev.train_run_id, ev.event_time.isoformat(), prev[1], ev.location.km))
        last[ev.train_run_id] = (t, ev.location.km)
    assert intervals > 40_000  # every train, every tick of the day
    assert jumps == [], jumps[:5]
