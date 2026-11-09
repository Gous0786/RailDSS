"""Offline rule auditor over a twin ground-truth day (FR-VER-12; issue #78).

The test sits outside the verifier package: it may run the twin to produce a trace, while the
verifier itself never imports the twin (NFR-MNT-02, import-linter).
"""

from __future__ import annotations

import datetime as dt

import pytest

from raildss_twin import run
from raildss_twin.plans import day_start
from raildss_twin.sim import RunReport, token
from raildss_verifier import network
from raildss_verifier.auditor import AuditReport, TrainMeta, audit
from raildss_verifier.model import Status

DAY = dt.date(2025, 8, 20)


@pytest.fixture(scope="module")
def day_audit() -> tuple[AuditReport, RunReport]:
    world = run.load_world()
    report, sim = run.run_day(world, DAY, keep_events=True)
    metas = {
        token(t.plan.run_id): TrainMeta(
            kind=t.plan.kind,
            length_m=t.plan.template.length_m,
            electric=t.plan.template.loco.traction == "electric",
            booked_dep={n.op: n.dep for n in t.plan.nodes if n.stop} if t.plan.timetabled else {},
        )
        for t in sim.trains
    }
    net = network.load(world.layout.network_version)
    return audit(sim.events, metas, net, day_start(DAY)), report


def test_the_auditor_runs_the_catalogue_over_every_train_of_a_twin_day(
    day_audit: tuple[AuditReport, RunReport],
) -> None:
    result, twin = day_audit
    assert result.trains == twin.entered > 0
    results = result.report.by_rule()
    assert Status.ERROR not in {r.status for r in results.values()}
    # every block-section and station rule saw movements from the trace
    for rule_id in ("VER-BLK-01", "VER-BLK-02", "VER-HDW-01", "VER-CON-01", "VER-CAP-02", "VER-LSP-01"):
        assert results[rule_id].status is Status.PASS, results[rule_id].violations[:3]


def test_no_unverified_rule_fails_on_the_twin_day(day_audit: tuple[AuditReport, RunReport]) -> None:
    result, _ = day_audit
    for r in result.report.results:
        if r.citation.verification_status == "U":
            assert r.status is not Status.FAIL


def test_executed_violations_are_reported_by_rule(day_audit: tuple[AuditReport, RunReport]) -> None:
    """The BL-0 twin checks CSR only at booked stops, so a train held on a short line is an executed
    violation the auditor must report (FR-VER-12: logged as a safety-relevant defect)."""
    result, _ = day_audit
    failing = {r.rule_id for r in result.report.results if r.status is Status.FAIL}
    assert failing <= {"VER-XNG-02"}
    assert result.executed_violations == sum(result.by_rule.get(r, 0) for r in failing)
    for v in result.report.by_rule()["VER-XNG-02"].violations:
        assert "stands on" in v.detail
