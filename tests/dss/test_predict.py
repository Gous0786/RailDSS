"""DS-4 prediction: process times and ETA propagation (issue #72, spec FR-PRED-03/04/05).

The live part runs on a 4 h twin window (20 Aug 2025, D0) through DS-1 and DS-3; the process-time tables
and the evaluation use the real freight log. Only the tests import the twin.
"""

from __future__ import annotations

import datetime as dt
import time
from typing import Any

import pytest

from raildss_dss.ingest import Ingestor, MemoryTransport
from raildss_dss.predict import (
    EVAL_DOC,
    METHODS,
    CycleError,
    Ordering,
    Predictor,
    ProcessTimes,
    evaluate,
    log_traces,
    split_days,
)
from raildss_dss.state import PlanDirectory, StateEstimator
from raildss_twin import run
from raildss_twin.observe import Observer
from raildss_twin.plans import day_start

DAY = dt.date(2025, 8, 20)
HOUR = 3600.0
NOW = day_start(DAY) + dt.timedelta(hours=3)
LIMIT_S = 2.0  # FR-PRED-04: all trains in a 120 min horizon in < 2 s


@pytest.fixture(scope="module")
def setup() -> dict[str, Any]:
    from raildss_etl.load import run_stage1

    world = run.load_world()
    reg = world.register
    directory = PlanDirectory.from_published(register=reg)
    traces = log_traces(run_stage1(write=False)[0], directory)
    report, process = evaluate(traces, directory, reg)
    obs = Observer(reg, "D0", keep=True)
    run.run_day(world, DAY, until_s=4 * HOUR, observers=[obs])
    ing = Ingestor(reg)
    ing.run(MemoryTransport([(t, e.to_json()) for t, e in obs.events]))
    est = StateEstimator(directory, reg)
    for e in sorted(ing.released, key=lambda e: e.event_time):
        if e.event_time <= NOW:
            est.apply(e)
    return {
        "reg": reg,
        "directory": directory,
        "traces": traces,
        "report": report,
        "process": process,
        "est": est,
    }


def predictor(setup: dict[str, Any]) -> Predictor:
    return Predictor(setup["process"], setup["reg"])


# --------------------------------------------------------------------------- FR-PRED-04
def test_every_train_in_the_horizon_gets_p50_and_p90_for_every_event(setup: dict[str, Any]) -> None:
    p = predictor(setup)
    t0 = time.perf_counter()
    preds = p.predict(setup["est"], NOW)
    assert time.perf_counter() - t0 < LIMIT_S
    snap = setup["est"].snapshot(NOW)
    by_train: dict[str, list[Any]] = {}
    for e in preds:
        by_train.setdefault(e.run_id, []).append(e)
    moving = [s for s in snap.trains if setup["directory"].path(s.run_id)]
    assert len(moving) >= 5
    for s in moving:
        events = by_train.get(s.run_id, [])
        path = setup["directory"].path(s.run_id)
        at_end = setup["est"].trains[s.run_id].station == path[-1]
        assert events or at_end, f"no prediction for {s.run_id}"
        # every event of the remaining path up to the horizon, in path order, P10 <= P50 <= P90
        times = [e.eta_p50 for e in events]
        assert times == sorted(times)
        for e in events:
            assert NOW <= e.eta_p50 <= e.eta_p90
            assert e.eta_p50 <= NOW + p.horizon
            if "@" in e.run_id and e.kind == "DEP" and e.booked is not None:
                assert e.eta_p50 >= e.booked  # a booked train never leaves early
    assert any(e.eta_p90 > e.eta_p50 for e in preds)  # freight spread


def _shared_block(preds: list[Any], directory: PlanDirectory) -> tuple[str, str, str]:
    deps = {(e.run_id, e.station) for e in preds if e.kind == "DEP"}
    arrs = {(e.run_id, e.station) for e in preds if e.kind == "ARR"}
    users: dict[str, list[str]] = {}
    for (a, b), block in directory.blocks.items():
        for run_id, station in deps:
            if station == a and (run_id, b) in arrs:
                users.setdefault(block, []).append(run_id)
    block, runs = next((k, sorted(set(v))) for k, v in sorted(users.items()) if len(set(v)) >= 2)
    return block, runs[0], runs[1]


def test_propagation_respects_the_plan_orderings(setup: dict[str, Any]) -> None:
    p = predictor(setup)
    free = p.predict(setup["est"], NOW)
    block, first, second = _shared_block(free, setup["directory"])
    a, b = next(k for k, v in setup["directory"].blocks.items() if v == block)
    planned = p.predict(setup["est"], NOW, [Ordering(first, second, block)])
    get = {(e.run_id, e.station, e.kind): e for e in planned}
    left, enters = get[(first, b, "ARR")], get[(second, a, "DEP")]
    assert enters.eta_p50 >= left.eta_p50 and enters.eta_p90 >= left.eta_p90
    before = {(e.run_id, e.station, e.kind): e for e in free}
    assert all(get[k].eta_p50 >= before[k].eta_p50 for k in get if k in before)  # an ordering only delays
    with pytest.raises(CycleError):
        p.predict(setup["est"], NOW, [Ordering(first, second, block), Ordering(second, first, block)])


# --------------------------------------------------------------------------- FR-PRED-03
def test_freight_tables_are_trained_on_twenty_days_with_fallbacks(setup: dict[str, Any]) -> None:
    train, test = split_days(setup["traces"], int(setup["reg"].value("pred.train_days")))
    assert len(train) == 20 and len(test) == 7 and max(train) < min(test)
    process: ProcessTimes = setup["process"]
    assert process.runs and all(q.n >= process.min_samples for q in process.runs.values())
    assert all(q.p10 <= q.p50 <= q.p90 for q in process.runs.values())
    blocks = setup["directory"].blocks
    sparse = next(((a, b) for (a, b), blk in blocks.items() if (blk, False) not in process.runs), None)
    assert sparse is not None  # some blocks have fewer than min_samples empty runs
    q = process.freight_run(*sparse, loaded=False)
    assert q.source == "fallback" and q.p50 > 0


# --------------------------------------------------------------------------- FR-PRED-05
def test_evaluation_reports_mae_per_horizon_against_both_baselines(setup: dict[str, Any]) -> None:
    report = setup["report"]
    horizons = [float(h) for h in setup["reg"].value("pred.eval_horizons_min")]
    assert [s.horizon_min for s in report.scores] == horizons
    for s in report.scores:
        assert s.n > 0 and all(s.mae(m) is not None for m in METHODS)
    thirty = next(s for s in report.scores if s.horizon_min == 30)
    assert thirty.mae("model") <= thirty.mae("persistence")  # FR-PRED-05 [SA] target
    assert EVAL_DOC.read_text(encoding="utf-8") == report.render_markdown()  # committed report is current
