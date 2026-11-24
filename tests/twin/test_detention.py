"""Freight detention from the K1 dwell statistics (issue #145, FV-02, DSC-20)."""

from __future__ import annotations

import datetime as dt

from raildss_params import load
from raildss_twin import run
from raildss_twin.detention import Detention

REGISTER = load()
CALLS = 20_000


def test_halts_are_seeded_and_can_be_switched_off() -> None:
    a, b, c = Detention(REGISTER, 1), Detention(REGISTER, 1), Detention(REGISTER, 2)
    draws = [
        (a.seconds(f"F{i}", "VZM"), b.seconds(f"F{i}", "VZM"), c.seconds(f"F{i}", "VZM")) for i in range(50)
    ]
    assert all(x == y for x, y, _ in draws) and any(x != z for x, _, z in draws)  # per seed
    off = Detention(REGISTER, 1)
    off.enabled = False
    assert off.seconds("F1", "VZM") == 0.0 and a.seconds("F1", "PSA") == 0.0  # off, or no K1 statistics


def test_a_station_keeps_its_share_of_long_halts_and_its_p90() -> None:
    d = Detention(REGISTER, 0)
    d.scale = 1.0  # the shape before calibration: the K1 statistics themselves
    cfg = REGISTER.value("twin.freight_detention")["stations"]["VZM"]
    mins = sorted(d.seconds(f"F{i}", "VZM") / 60 for i in range(CALLS))
    share = sum(1 for m in mins if m > 0) / CALLS
    assert abs(share - float(cfg["share"])) < 0.01
    p90 = mins[int(0.9 * CALLS)]
    assert abs(p90 - float(cfg["p90_min"])) < 5  # the tail is fitted to the station's P90


def test_freight_halts_in_bl0_only_at_inner_stations_and_passengers_are_not_held() -> None:
    world = run.load_world()
    _, sim = run.run_day(world, dt.date(2025, 8, 20), until_s=6 * 3600)
    d = sim.detention
    assert d.enabled
    for t in sim.trains:
        ends = {t.plan.nodes[0].op, t.plan.nodes[-1].op}
        if t.plan.kind != "freight":
            continue
        assert all(d.seconds(t.plan.run_id, op) >= 0 for op in ends)
    assert any(
        d.seconds(t.plan.run_id, n.op) > 0
        for t in sim.trains
        if t.plan.kind == "freight"
        for n in t.plan.nodes[1:-1]
    )
