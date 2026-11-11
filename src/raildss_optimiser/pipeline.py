"""End to end on a twin day (issue #75): the DSS detects conflicts, the greedy dispatcher recommends.

twin day (observed under a profile) -> DS-1 ingestion -> DS-3 state ``S_t`` -> DS-4 ETAs -> DS-5 conflicts
-> greedy plan from ``t`` -> one recommendation per conflict.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from raildss_dss.conflicts import Conflict, Detector, occupations
from raildss_dss.ingest import Ingestor, MemoryTransport
from raildss_dss.predict import Predictor, ProcessTimes
from raildss_dss.state import PlanDirectory, StateEstimator
from raildss_optimiser.greedy import GreedyDispatcher, GreedyPlan, Recommendation, recommend
from raildss_twin import run
from raildss_twin.observe import Observer
from raildss_twin.plans import day_start
from raildss_twin.sim import Closure


@dataclass
class Outcome:
    at: dt.datetime
    conflicts: list[Conflict]
    plan: GreedyPlan
    recommendations: list[Recommendation]


def recommend_at(
    world: run.World,
    day: dt.date,
    at_s: float,
    *,
    profile: str = "D0",
    closures: Sequence[Closure] = (),
    exit_windows: Mapping[str, list[tuple[float, float]]] | None = None,
) -> Outcome:
    """Run the DSS chain on the day up to ``at_s`` and recommend for the conflicts it detects."""
    reg = world.register
    obs = Observer(reg, profile, keep=True)
    run.run_day(world, day, until_s=at_s, observers=[obs])
    ing = Ingestor(reg)
    ing.run(MemoryTransport([(t, e.to_json()) for t, e in obs.events]))
    directory = PlanDirectory.from_published(register=reg)
    est = StateEstimator(directory, reg)
    now = day_start(day) + dt.timedelta(seconds=at_s)
    for e in sorted(ing.released, key=lambda e: e.event_time):
        if e.event_time <= now:
            est.apply(e)
    predictor = Predictor(ProcessTimes.from_log([], directory, reg), reg)
    preds = predictor.predict(est, now)
    occs = occupations(preds, est.snapshot(now), est, directory, predictor.horizon)
    conflicts = Detector.build(directory, reg).detect(occs, now)
    plan = GreedyDispatcher(world, reg).plan(day, at_s, closures=closures, exit_windows=exit_windows)
    return Outcome(now, conflicts, plan, recommend(conflicts, plan, day_start(day)))
