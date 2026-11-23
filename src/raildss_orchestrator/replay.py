"""Replay of a recorded recommendation (spec FR-AUD-03, NFR-AUD-01; issue #85).

A ``plan_run`` record holds the plan context, state hash and version, versions, seed and solver settings of
one planning cycle, and the candidate set with every verification result. :func:`replay_run` runs the cycle
again from the record and compares the canonical JSON, byte for byte:

* the greedy candidate is recomputed: the twin and the dispatcher are deterministic for a given context;
* a solver (CP-SAT) candidate is not solved again, its recorded plan is re-verified by the G3 sandbox replay
  (G1 is carried over from the record: the solution events are not stored);
* :func:`replay_recommendation` also re-derives the recommended orders from the replayed candidate;
* :func:`replay_sample` replays a seeded sample of the recommendations of a chain (DSC-12: 20 of 20
  reproduce exactly). Recommendations of one planning cycle share one replay of the cycle.
"""

from __future__ import annotations

import random
from typing import Any

from pydantic import BaseModel, Field

from raildss_audit.chain import AuditLog, AuditRecord, canonical
from raildss_verifier.gates import GateResult

from .auditing import (
    candidate_json,
    ctx_from_json,
    decisions_of,
    digest_of,
    holds_of,
)
from .loop import Orchestrator
from .model import Candidate, actions_for
from .twin import TwinPlanner


class ReplayResult(BaseModel):
    rec_id: str | None = None
    run_id: str
    identical: bool
    recorded_digest: str
    replayed_digest: str
    candidates: dict[str, bool] = Field(default_factory=dict)
    actions_identical: bool | None = None
    differences: list[str] = Field(default_factory=list)


def replay_run(record: AuditRecord, planner: TwinPlanner) -> tuple[ReplayResult, list[dict[str, Any]]]:
    """Run the recorded cycle again; returns the comparison and the replayed candidate records."""
    pl = record.payload
    ctx = ctx_from_json(pl["context"])
    recorded: list[dict[str, Any]] = pl["candidates"]
    greedy = TwinPlanner(planner.world, planner.day, cpsat_enabled=False, register=planner.reg)
    fresh = (
        {candidate_json(c)["name"]: candidate_json(c) for c in greedy.plan(ctx).candidates}
        if recorded
        else {}
    )
    replayed: list[dict[str, Any]] = []
    same: dict[str, bool] = {}
    for rc in recorded:
        if rc["solver"]:
            g3 = planner.rejudge(ctx, f"{ctx.plan_id}-{rc['name']}", decisions_of(rc), holds_of(rc))
            cand = Candidate(
                name=rc["name"],
                plan_id=rc["plan_id"],
                decisions=decisions_of(rc),
                holds=holds_of(rc),
                g1=GateResult.model_validate(rc["g1"]),
                g2=None,
                g3=g3,
            )
            got = candidate_json(cand)
        else:
            got = fresh.get(rc["name"], {})
        replayed.append(got)
        same[rc["name"]] = canonical(got) == canonical(rc)
    rec_digest, new_digest = str(pl["digest"]), digest_of(replayed)
    result = ReplayResult(
        run_id=str(pl["run_id"]),
        identical=rec_digest == new_digest,
        recorded_digest=rec_digest,
        replayed_digest=new_digest,
        candidates=same,
        differences=[f"candidate {n} differs" for n, ok in same.items() if not ok],
    )
    return result, replayed


Cycles = dict[str, tuple[ReplayResult, list[dict[str, Any]]]]  # plan_run id -> its replay


def replay_recommendation(
    log: AuditLog, rec_id: str, planner: TwinPlanner, *, cycles: Cycles | None = None
) -> ReplayResult:
    """Replay one recommendation from its record: same candidate set, verification results and orders.

    ``cycles`` caches the replay of each planning cycle, so a sample does not replay a cycle twice."""
    rows = log.query(rec_id=rec_id, kind="recommendation")
    if not rows:
        raise KeyError(f"no recommendation record {rec_id}")
    rec = rows[0].payload
    runs = [r for r in log.query(kind="plan_run") if r.payload["run_id"] == rec["plan_run"]]
    if not runs:
        raise KeyError(f"no plan_run record {rec['plan_run']}")
    key = str(rec["plan_run"])
    if cycles is not None and key in cycles:
        result, replayed = cycles[key][0].model_copy(deep=True), cycles[key][1]
    else:
        result, replayed = replay_run(runs[0], planner)
        if cycles is not None:
            cycles[key] = (result.model_copy(deep=True), replayed)
    result.rec_id = rec_id
    ctx = ctx_from_json(runs[0].payload["context"])
    cand = next((c for c in replayed if c.get("name") == rec["candidate"]), None)
    if cand is None:
        result.actions_identical, result.identical = False, False
        result.differences.append(f"candidate {rec['candidate']} was not reproduced")
        return result
    conflict = rec["conflict"]
    orders = [
        a
        for a in actions_for(decisions_of(cand), holds_of(cand), conflict["trains"], conflict["resource"])
        if not Orchestrator._in_orders(a, ctx.commit_holds, ctx.commit_lines)
    ]
    again = [a.model_dump(mode="json") for a in orders]
    result.actions_identical = canonical(again) == canonical(rec["actions"])
    if not result.actions_identical:
        result.identical = False
        result.differences.append("the recommended orders differ")
    return result


class SampleResult(BaseModel):
    """DSC-12: a seeded sample of the recommendations of a chain, each replayed from its record."""

    requested: int
    available: int  # recommendations in the chain
    sampled: int
    identical: int
    seed: int
    results: list[ReplayResult]

    @property
    def all_identical(self) -> bool:
        return self.sampled > 0 and self.identical == self.sampled

    @property
    def meets_dsc12(self) -> bool:
        """The whole requested sample was taken and reproduced exactly (20 of 20)."""
        return self.all_identical and self.sampled >= self.requested


def replay_sample(log: AuditLog, planner: TwinPlanner, n: int, seed: int = 0) -> SampleResult:
    """Replay ``n`` recommendations drawn at random (seeded) from the chain; all of them if fewer."""
    recs = sorted(str(r.refs["rec_id"]) for r in log.query(kind="recommendation"))
    chosen = sorted(random.Random(seed).sample(recs, min(n, len(recs))))
    cycles: Cycles = {}
    results = [replay_recommendation(log, rec_id, planner, cycles=cycles) for rec_id in chosen]
    return SampleResult(
        requested=n,
        available=len(recs),
        sampled=len(chosen),
        identical=sum(r.identical for r in results),
        seed=seed,
        results=results,
    )
