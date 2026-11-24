"""The offline auditor on the incident scenarios (issue #151; DSC-08, DSC-24).

SCN-04, SCN-05, SCN-20 and SCN-21 (single-line working) and SCN-03, SCN-07 and SCN-15 (speed restrictions)
run with their scripted disruptions (BL-0). They are audited with the incidents in force as plan context,
against the network as worked: zero executed violations, with the new rules applied."""

from __future__ import annotations

import pytest

from raildss_twin import run, sandbox, scenario
from raildss_twin.plans import day_start
from raildss_verifier import network
from raildss_verifier.auditor import audit
from raildss_verifier.model import CandidatePlan, Status

INCIDENT_RULES = {"VER-SPD-01", "VER-SLW-01", "VER-SLW-02", "VER-EXT-01"}


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.mark.parametrize("sid", ["SCN-04", "SCN-05", "SCN-20", "SCN-21", "SCN-03", "SCN-07", "SCN-15"])
def test_an_incident_scenario_runs_with_zero_executed_violations(world: run.World, sid: str) -> None:
    sc = next(s for s in scenario.load_library_v1() if s.id == sid)
    kw = scenario.run_kwargs(sc)
    _, sim = run.run_day(
        world,
        sc.wtt_date,
        keep_events=True,
        directives=kw["directives"],
        seed=kw["seed"],
        day_instance=kw["day_instance"],
        setup=kw["setup"],
    )
    net = network.load(world.layout.network_version)
    ctx = sandbox.incident_context(sim, net)
    context = CandidatePlan(plan_id=sid, network_version=net.version, trains=[], **ctx)
    worked = network.as_worked(net, sim.slw_log)
    result = audit(
        sim.events,
        sandbox.train_meta(sim),
        worked,
        day_start(sc.wtt_date),
        register=world.register,
        context=context,
    )
    applied = {r.rule_id for r in result.report.results if r.status is not Status.NOT_APPLICABLE}
    executed = {r.rule_id: len(r.violations) for r in result.report.results if r.violations}
    assert result.executed_violations == 0, executed
    # SCN-21 blocks one KTV-VZM automatic line: the other two remain, there is no single-line working
    if sid in ("SCN-04", "SCN-05", "SCN-20"):
        assert ctx["single_line"] and {"VER-SLW-01", "VER-SLW-02"} <= applied & INCIDENT_RULES
    if sid in ("SCN-03", "SCN-07", "SCN-15"):  # a TSR or caution working (with a paper line clear)
        assert ctx["restrictions"] and "VER-SPD-01" in applied
