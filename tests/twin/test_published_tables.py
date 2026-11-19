"""train_run and the rolling-stock tables are published and read by the twin (issue #66; FR-TWIN-02)."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import pytest

from raildss_etl import cli
from raildss_etl.timetable import Timetable, ist
from raildss_twin import run
from raildss_twin.plans import passenger_plans

VERSION = "KTV-PSA-2025-08-r2"
DAY = dt.date(2025, 8, 20)
TABLES = ("train_run", "loco_class", "vehicle_type", "consist")


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


def test_network_show_counts_the_new_tables(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["network", "show", VERSION]) == 0
    out = capsys.readouterr().out
    for table in TABLES:
        m = re.search(rf"\b{table}\b\D*(\d+)", out)
        assert m is not None and int(m.group(1)) > 0, table


def test_train_runs_cover_the_dataset_days_with_their_consists(world: run.World) -> None:
    runs: list[Any] = list(world.tables["train_run"])
    consists = {c.id for c in world.tables["consist"]}
    assert {r.kind for r in runs} == {"passenger"}
    assert len({r.run_id for r in runs}) == len(runs)
    assert {dt.date(2025, 8, d) for d in range(1, 28)} <= {r.run_date for r in runs}
    assert all(r.consist_id in consists for r in runs)


def test_the_twin_uses_the_published_rolling_stock(world: run.World) -> None:
    stock = world.timing.stock
    published = {r.code: r for r in world.tables["loco_class"]}
    assert stock.loco_classes == published
    assert all(stock.loco_classes[c] is published[c] for c in published)
    tpl = stock.passenger(22)
    record = next(c for c in world.tables["consist"] if c.id == tpl.consist.id)
    assert tpl.consist is record  # the published record, not a rebuilt one


def test_the_twin_expands_a_day_from_the_published_train_runs(
    world: run.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected, _ = passenger_plans(world.tables, world.layout, world.timing, DAY)

    def no_rebuild(*_: Any, **__: Any) -> Any:
        raise AssertionError("the day is published: runs_on must not rebuild it")

    monkeypatch.setattr(Timetable, "runs_on", no_rebuild)
    plans, _ = passenger_plans(world.tables, world.layout, world.timing, DAY)
    assert [p.run_id for p in plans] == [p.run_id for p in expected] and plans
    with pytest.raises(AssertionError, match="must not rebuild"):  # a day it does not cover is rebuilt
        passenger_plans(world.tables, world.layout, world.timing, dt.date(2025, 9, 20))


def test_published_runs_match_the_calendar_expansion(world: run.World) -> None:
    tt = Timetable(
        services=list(world.tables["service"]),  # type: ignore[arg-type]
        points=list(world.tables["timetable_point"]),  # type: ignore[arg-type]
        busy_days={
            f"{p.train_id}/{p.seq}": [x == "1" for x in str(p.raw["BLOCK_BUSY_DAYS"]).split(",")]
            for p in world.tables["timetable_point"]  # type: ignore[attr-defined]
        },
    )
    rebuilt = {r.run_id for r in tt.runs_on(DAY, ist(), VERSION)}
    published = {r.run_id for r in world.tables["train_run"] if tt.touches(r, DAY)}  # type: ignore[arg-type]
    assert rebuilt == published and rebuilt
