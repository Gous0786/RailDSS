"""Special-train insertion (SCN-11) and a closed boundary exit (SCN-23) in the twin (issue #148)."""

from __future__ import annotations

import pytest

from raildss_twin import run, scenario
from raildss_twin.plans import day_start
from raildss_twin.sim import Sim, token

LIBRARY = {s.id: s for s in scenario.load_library_v1()}
MIN = 60.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def test_the_special_train_becomes_an_extra_plan_with_an_entry_time_only(world: run.World) -> None:
    sc = LIBRARY["SCN-11"]
    (plan,) = scenario.special_plans(world, sc)
    assert plan.run_id == "SPL-11@2025-08-18" and plan.kind == "passenger" and not plan.timetabled
    assert plan.category == "mail_express" and plan.entry_s == 13 * 3600.0
    assert plan.nodes[0].op == "KTV" and plan.nodes[-1].op == "PSA"  # DN: entry station to the section end
    assert all(n.arr == n.dep == plan.entry_s and not n.stop for n in plan.nodes)
    kw = scenario.run_kwargs(sc, world)
    day_plans, _, _ = run.plans_for(world, sc.wtt_date, sc.freight_day)
    assert len(kw["plans"]) == len(day_plans) + 1  # the day's own plans are all still there
    assert "plans" not in scenario.run_kwargs(sc)  # no world: no insertion (other scenarios need none)


def test_an_up_special_train_runs_from_its_entry_station_towards_the_start(world: run.World) -> None:
    sc = LIBRARY["SCN-11"]
    up = sc.model_copy(
        update={"disruptions": [sc.disruptions[0].model_copy(update={"direction": "UP", "station": "PSA"})]}
    )
    (plan,) = scenario.special_plans(world, up)
    assert plan.nodes[0].op == "PSA" and plan.nodes[-1].op == "KTV"


def test_scn_11_runs_the_special_train_through_the_section_at_its_entry_time(world: run.World) -> None:
    sc = LIBRARY["SCN-11"]
    report, sim = run.run_day(world, sc.wtt_date, keep_events=True, **scenario.run_kwargs(sc, world))
    assert report.completed and not report.stuck and not report.deadlocks
    origin = day_start(sc.wtt_date)
    events = [e for e in sim.events if e.train_run_id == token("SPL-11@2025-08-18")]
    states = [e for e in events if e.event_type.value == "TRAIN_STATE" and e.location is not None]
    assert states
    assert (states[0].event_time - origin).total_seconds() == 13 * 3600.0  # enters at 13:00 at KTV
    first, last = states[0].location, states[-1].location
    assert first is not None and last is not None
    assert first.km == 0.0 and last.km == pytest.approx(176.83)  # and reaches PSA


def test_the_special_train_is_in_the_bl0_kpis_with_its_class_weight(world: run.World) -> None:
    from raildss_orchestrator.scenarios import bl0

    sc = LIBRARY["SCN-11"]
    report = bl0(world, sc)
    row = next(t for t in report.trains if t.run_id == token("SPL-11@2025-08-18"))
    assert row.weight == 5.0 and row.kind == "passenger" and row.exit_s is not None  # mail_express


def test_a_closed_exit_is_a_window_at_the_boundary_station_drawn_from_the_seed() -> None:
    sc = LIBRARY["SCN-23"]
    windows = scenario.exit_windows(sc)
    ((station, [(start, end)]),) = windows.items()
    assert station == "KTV" and start == 14 * 3600.0  # the KTV-VSKP boundary, 14:00
    assert 90 * MIN <= end - start <= 120 * MIN
    assert windows == scenario.exit_windows(sc)  # deterministic
    assert scenario.exit_windows(sc.model_copy(update={"seed": sc.seed + 1})) != windows


def test_scn_23_no_train_leaves_at_ktv_during_the_window_and_some_are_held(world: run.World) -> None:
    sc = LIBRARY["SCN-23"]
    ((_, [(start, end)]),) = scenario.exit_windows(sc).items()
    plans, _, _ = run.plans_for(world, sc.wtt_date, sc.freight_day)
    leaving = {token(p.run_id) for p in plans if p.nodes[-1].op == "KTV"}
    origin = day_start(sc.wtt_date)

    def exits(sim: Sim) -> dict[str, float]:  # the last release of the section, per train leaving at KTV
        return {
            e.train_run_id: (e.event_time - origin).total_seconds()
            for e in sim.events
            if e.event_type.value == "TDS_OCCUPANCY"
            and not e.payload.get("occupied")
            and e.train_run_id in leaving
        }

    _, closed = run.run_day(world, sc.wtt_date, keep_events=True, **scenario.run_kwargs(sc, world))
    _, plain = run.run_day(world, sc.wtt_date, keep_events=True, seed=sc.seed, day_instance=sc.freight_day)
    after, before = exits(closed), exits(plain)
    assert after and not [t for t in after.values() if start <= t < end]
    held = [after[k] - before[k] for k in after if k in before and after[k] - before[k] > 30 * MIN]
    assert held  # a train that would have left in the window waits at the station before KTV
