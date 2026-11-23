"""Audit and replay of the loop on a real twin day (issue #85; FR-AUD-01..03).

20 Aug 2025 from 08:00: the loop records its alerts, plans, recommendations, decisions, orders and
acknowledgements in a hash-chained log; a recorded recommendation is then replayed from its record.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from raildss_audit.chain import AuditLog
from raildss_orchestrator.auditing import AuditRecorder
from raildss_orchestrator.loop import Orchestrator
from raildss_orchestrator.replay import replay_recommendation
from raildss_orchestrator.twin import TwinDss, TwinPlanner
from raildss_twin import run
from raildss_twin.plans import day_start

DAY = dt.date(2025, 8, 20)
START_S = 8 * 3600.0


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def recorded(world: run.World, tmp_path_factory: pytest.TempPathFactory) -> tuple[Orchestrator, Path]:
    root = tmp_path_factory.mktemp("audit")
    planner = TwinPlanner(world, DAY, alternatives=0)
    recorder = AuditRecorder(
        AuditLog(root, "KTV-PSA"), world.register, day_start(DAY), day=DAY, solver=planner.solver_info()
    )
    loop = Orchestrator(world.register, TwinDss(world, DAY, until_s=START_S + 3600), planner, audit=recorder)
    loop.run(START_S, START_S + 120.0, 120.0)
    recorder.log.anchor(recorder.log.days()[0])
    return loop, root


def test_the_loop_records_everything_in_an_intact_chain(recorded: tuple[Orchestrator, Path]) -> None:
    loop, root = recorded
    log = AuditLog(root, "KTV-PSA")
    assert log.verify().ok
    kinds = {r.kind for r in log.records()}
    assert {"alert", "plan_run", "recommendation", "decision", "order"} <= kinds
    assert loop.audit is not None and loop.audit.completeness(loop.lc) == []
    run_rec = log.query(kind="plan_run")[0]
    assert run_rec.payload["state"]["hash"] and run_rec.payload["versions"]["rule_catalogue"]
    assert {c["name"] for c in run_rec.payload["candidates"]} >= {"greedy", "cpsat"}


def test_a_recorded_recommendation_is_reproduced_byte_for_byte(
    world: run.World, recorded: tuple[Orchestrator, Path]
) -> None:
    _, root = recorded
    log = AuditLog(root, "KTV-PSA")
    planner = TwinPlanner(world, DAY, cpsat_enabled=False)
    sample = [r.refs["rec_id"] for r in log.query(kind="recommendation")][:2]
    assert sample
    for rec_id in sample:
        result = replay_recommendation(log, rec_id, planner)
        assert result.identical, result.differences
        assert result.recorded_digest == result.replayed_digest
        assert result.actions_identical is True
        assert (
            all(result.candidates.values()) and "cpsat" in result.candidates
        )  # the solver plan is re-verified


def test_tampering_with_one_record_breaks_the_chain_and_is_detected(
    recorded: tuple[Orchestrator, Path],
) -> None:
    _, root = recorded
    path = next((root / "KTV-PSA").glob("*.jsonl"))
    original = path.read_text(encoding="utf-8")
    try:
        lines = original.splitlines()
        target = next(i for i, line in enumerate(lines) if '"kind":"recommendation"' in line.replace(" ", ""))
        lines[target] = lines[target].replace("hold", "HOLD", 1)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        result = AuditLog(root, "KTV-PSA").verify()
        assert not result.ok and any("hash" in p or "anchor" in p for p in result.problems)
    finally:
        path.write_text(original, encoding="utf-8")
    assert AuditLog(root, "KTV-PSA").verify().ok


def test_a_seeded_sample_of_recommendations_reproduces_exactly(
    world: run.World, recorded: tuple[Orchestrator, Path]
) -> None:
    """Issue #164 (DSC-12): replay a seeded sample of the chain's recommendations, each from its record."""
    from raildss_orchestrator.replay import replay_sample

    _, root = recorded
    log = AuditLog(root, "KTV-PSA")
    planner = TwinPlanner(world, DAY, cpsat_enabled=False)
    available = len(log.query(kind="recommendation"))
    assert available >= 1
    sample = replay_sample(log, planner, 20, seed=7)
    assert (sample.requested, sample.available, sample.sampled) == (20, available, min(20, available))
    assert sample.identical == sample.sampled and sample.all_identical, [
        (r.rec_id, r.differences) for r in sample.results if not r.identical
    ]
    assert sample.meets_dsc12 is (available >= 20)  # a short chain cannot meet the 20 of 20
    one = replay_sample(log, planner, 1, seed=7)
    assert one.meets_dsc12 and one.sampled == 1
    assert [r.rec_id for r in replay_sample(log, planner, 1, seed=7).results] == [
        r.rec_id for r in one.results
    ]


def test_the_audit_replay_command_samples_and_reports_the_determinism(
    recorded: tuple[Orchestrator, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    from raildss_orchestrator.__main__ import main

    _, root = recorded
    assert main(["audit-replay", "--audit-dir", str(root), "--sample", "1", "--seed", "3"]) == 0
    out = capsys.readouterr().out
    assert "replay determinism: 1 / 1 sampled recommendations reproduce exactly" in out
    assert "DSC-12 met" in out
    with pytest.raises(SystemExit, match="--rec REC-0001 or --sample N"):
        main(["audit-replay", "--audit-dir", str(root)])
