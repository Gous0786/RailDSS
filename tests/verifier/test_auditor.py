"""Offline rule auditor over twin ground-truth days (FR-VER-12, FR-TWIN-10; issues #78, #51, #101).

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
REAL_DAYS = [dt.date(2025, 8, d) for d in range(1, 28)]  # the 27 real freight days (aug25-day-01..27)


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def net(world: run.World) -> network.Network:
    return network.load(world.layout.network_version)


def audit_day(world: run.World, net: network.Network, day: dt.date) -> tuple[AuditReport, RunReport]:
    report, sim = run.run_day(world, day, keep_events=True)
    metas = {
        token(t.plan.run_id): TrainMeta(
            kind=t.plan.kind,
            length_m=t.plan.template.length_m,
            electric=t.plan.template.loco.traction == "electric",
            booked_dep={n.op: n.dep for n in t.plan.nodes if n.stop} if t.plan.timetabled else {},
        )
        for t in sim.trains
    }
    return audit(sim.events, metas, net, day_start(day)), report


@pytest.fixture(scope="module")
def day_audit(world: run.World, net: network.Network) -> tuple[AuditReport, RunReport]:
    return audit_day(world, net, DAY)


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


@pytest.mark.parametrize("day", REAL_DAYS, ids=lambda d: d.isoformat())
def test_a_real_day_runs_with_zero_executed_violations(
    world: run.World, net: network.Network, day: dt.date
) -> None:
    """FR-TWIN-10 acceptance: the auditor finds 0 executed violations, in particular 0 VER-XNG-02 (#101)."""
    result, twin = audit_day(world, net, day)
    assert twin.completed
    failing = {
        r.rule_id: r.violations[:2] for r in result.report.results if r.status in (Status.FAIL, Status.ERROR)
    }
    assert result.executed_violations == 0, failing
    assert result.by_rule.get("VER-XNG-02", 0) == 0
