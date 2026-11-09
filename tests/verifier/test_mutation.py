"""G2 rule checker: mutation tests over a synthetic network (FR-VER-03, FR-VER-04, FR-VER-10; §12.3).

A valid base plan passes every rule. Each mutant injects one fault and the rule it targets must flag
it; the catch rate must be 100 %. The scenario names are the ``tests`` of the rule catalogue.

Network (km): A 0 -- B 5 -- C 10 -- D 15. A-B is a single line ("1", both directions, absolute
block); B-C is double line with automatic signalling (two sub-blocks per line); C-D is double line,
absolute block. B has no crossing facility; D line 3 is not electrified.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from raildss_params import Register, load
from raildss_verifier import catalogue as cat
from raildss_verifier import network
from raildss_verifier.engine import check
from raildss_verifier.model import (
    Action,
    BlockRun,
    CandidatePlan,
    FrozenCall,
    MaintenanceBlock,
    Overall,
    Severity,
    StationCall,
    Status,
    SubRun,
    TrainPlan,
)
from raildss_verifier.network import Block, Line, Network, Station, Track

REGISTER: Register = load()
CATALOGUE = cat.load()
GAP = (500 + 180) / (20 / 3.6)  # T1 (500 m) + adequate distance at the clearing speed, s


def toy_network() -> Network:
    stations = {
        "A": Station("A", 0.0, True),
        "B": Station("B", 5.0, False),
        "C": Station("C", 10.0, True),
        "D": Station("D", 15.0, True),
    }
    blocks: dict[str, Block] = {}
    lines: dict[tuple[str, str], Line] = {}
    for a, b, system, line_ids, vmax in (
        ("A", "B", "ABS_absolute", {"1": "BOTH"}, 100.0),
        ("B", "C", "AUTO", {"DN": "DN", "UP": "UP"}, 110.0),
        ("C", "D", "ABS_absolute", {"DN": "DN", "UP": "UP"}, 100.0),
    ):
        for x, y, d in ((a, b, "DN"), (b, a, "UP")):
            blocks[f"{x}-{y}"] = Block(f"{x}-{y}", x, y, d, system)
            for lid, ld in line_ids.items():
                lines[(f"{x}-{y}", lid)] = Line(f"{x}-{y}", lid, ld, system, 5000.0, vmax)
    tracks = {
        (t.op, t.line_no): t
        for t in (
            Track("A", 1, "main", "BOTH", 700, platform=True, platform_length_m=600),
            Track("A", 2, "loop", "BOTH", 700),
            Track("B", 1, "main", "BOTH", 700, platform=True, platform_length_m=600),
            Track("B", 2, "loop", "BOTH", 400),
            Track("C", 1, "main", "DN", 700, platform=True, platform_length_m=600),
            Track("C", 2, "main", "UP", 700, platform=True, platform_length_m=600),
            Track("C", 3, "loop", "BOTH", 700),
            Track("D", 1, "main", "DN", 700),
            Track("D", 2, "main", "UP", 700),
            Track("D", 3, "loop", "BOTH", 700, electrified=False),
        )
    }
    connections = set()
    for (op, no), track in tracks.items():
        for (bid, lid), line in lines.items():
            blk = blocks[bid]
            ok = line.direction in (blk.direction, "BOTH") and track.directions in (blk.direction, "BOTH")
            if ok and blk.to_op == op:
                connections.add((op, no, bid, lid, "receive"))
            if ok and blk.from_op == op:
                connections.add((op, no, bid, lid, "send"))
    subs = {(bid, lid): 2 for (bid, lid) in lines if blocks[bid].system == "AUTO"}
    return Network("TOY", stations, blocks, lines, tracks, connections, subs)


NET = toy_network()


def call(op: str, no: int, arr: float, dep: float, booked: float | None = None) -> StationCall:
    return StationCall(op=op, line_no=no, arr_s=arr, dep_s=dep, stop=booked is not None, booked_dep_s=booked)


def auto(block: str, line: str, enter: float, clear: float) -> BlockRun:
    mid = (enter + clear) / 2
    return BlockRun(
        block_id=block,
        line_id=line,
        enter_s=enter,
        clear_s=clear,
        sub_blocks=[SubRun(seq=1, enter_s=enter, clear_s=mid), SubRun(seq=2, enter_s=mid, clear_s=clear)],
    )


def base() -> CandidatePlan:
    t1 = TrainPlan(
        run_id="T1",
        kind="passenger",
        length_m=500,
        calls=[
            call("A", 1, 0, 60),
            call("B", 1, 360, 480, 480),
            call("C", 1, 780, 900, 900),
            call("D", 1, 1200, 1260),
        ],
        runs=[
            BlockRun(block_id="A-B", line_id="1", enter_s=60, clear_s=360),
            auto("B-C", "DN", 480, 780),
            BlockRun(block_id="C-D", line_id="DN", enter_s=900, clear_s=1200),
        ],
    )
    t3 = TrainPlan(
        run_id="T3",
        kind="freight",
        length_m=600,
        calls=[
            call("A", 1, 1000, 1060),
            call("B", 1, 1360, 1420),
            call("C", 3, 1720, 1780),
            call("D", 1, 2080, 2140),
        ],
        runs=[
            BlockRun(block_id="A-B", line_id="1", enter_s=1060, clear_s=1360),
            auto("B-C", "DN", 1420, 1720),
            BlockRun(block_id="C-D", line_id="DN", enter_s=1780, clear_s=2080),
        ],
    )
    t2 = TrainPlan(
        run_id="T2",
        kind="freight",
        length_m=600,
        calls=[
            call("D", 2, 3000, 3060),
            call("C", 2, 3360, 3420),
            call("B", 1, 3720, 3780),
            call("A", 1, 4080, 4140),
        ],
        runs=[
            BlockRun(block_id="D-C", line_id="UP", enter_s=3060, clear_s=3360),
            auto("C-B", "UP", 3420, 3720),
            BlockRun(block_id="B-A", line_id="1", enter_s=3780, clear_s=4080),
        ],
    )
    return CandidatePlan(
        plan_id="base",
        network_version="TOY",
        trains=[t1, t3, t2],
        actions=[Action(run_id="T3", kind="hold", time_s=1360)],
        maintenance=[MaintenanceBlock(block_id="C-D", line_id="UP", start_s=0, end_s=2000)],
        frozen=[FrozenCall(run_id="T1", op="B", line_no=1, dep_s=480)],
    )


def train(p: CandidatePlan, run_id: str) -> TrainPlan:
    return next(t for t in p.trains if t.run_id == run_id)


def _set(p: CandidatePlan, run_id: str, what: str, i: int, **values: float | int | str) -> None:
    item = getattr(train(p, run_id), what)[i]
    for k, v in values.items():
        setattr(item, k, v)


def adequate_gap_exact(p: CandidatePlan) -> None:
    """T3 enters the single line exactly when T1 has cleared it plus adequate distance."""
    _set(p, "T3", "calls", 0, arr_s=360 + GAP - 60, dep_s=360 + GAP)
    _set(p, "T3", "runs", 0, enter_s=360 + GAP)


def _sub(p: CandidatePlan, run_id: str, run: int, sub: int, **values: float) -> None:
    item = train(p, run_id).runs[run].sub_blocks[sub]
    for k, v in values.items():
        setattr(item, k, v)


def _short(p: CandidatePlan) -> None:
    train(p, "T1").length_m = 650


Mutant = Callable[[CandidatePlan], None]
POSITIVE: dict[str, Mutant] = {"base": lambda p: None, "adequate_gap_exact": adequate_gap_exact}
MUTANTS: dict[str, Mutant] = {
    "blk01_two_in_section": lambda p: _set(p, "T3", "runs", 0, enter_s=300),
    "blk02_inside_adequate_distance": lambda p: _set(p, "T3", "runs", 0, enter_s=360 + GAP - 20),
    "blk04_opposing_on_single_line": lambda p: _set(p, "T2", "runs", 2, enter_s=1100, clear_s=1300),
    "hdw01_two_in_sub_block": lambda p: _sub(p, "T3", 1, 0, enter_s=600, clear_s=700),
    "xng01_precedence_without_facility": lambda p: _set(p, "T3", "calls", 1, line_no=2, arr_s=400, dep_s=450),
    "xng01_crossing_without_facility": lambda p: _set(p, "T2", "calls", 2, line_no=2, arr_s=400, dep_s=470),
    "xng02_short_loop": lambda p: _set(p, "T1", "calls", 1, line_no=2),
    "xng03_wrong_direction_line": lambda p: _set(p, "T2", "calls", 1, line_no=1),
    "con01_no_connection_edge": lambda p: _set(p, "T3", "runs", 2, line_id="UP"),
    "cap02_two_on_one_line": lambda p: _set(p, "T3", "calls", 2, line_no=1, arr_s=850, dep_s=880),
    "lsp01_too_fast": lambda p: _set(p, "T1", "runs", 0, clear_s=160),
    "plt01_non_platform_line": lambda p: _set(p, "T1", "calls", 2, line_no=3),
    "plt01_short_platform": _short,
    "pln01_early_departure": lambda p: _set(p, "T1", "calls", 1, dep_s=470),
    "mnt01_train_in_block": lambda p: (
        setattr(p.maintenance[0], "start_s", 3000.0) or setattr(p.maintenance[0], "end_s", 3500.0)
    ),
    "ele01_non_electrified_line": lambda p: _set(p, "T1", "calls", 3, line_no=3),
    "aut01_proceed_without_line_clear": lambda p: p.actions.append(
        Action(run_id="T1", kind="proceed_without_line_clear", time_s=60)
    ),
    "frz01_frozen_line_changed": lambda p: _set(p, "T1", "calls", 1, dep_s=540),
    "buf01_thin_buffer": lambda p: _set(p, "T3", "runs", 0, enter_s=360 + GAP + 60),
}


def scenario(name: str) -> CandidatePlan:
    p = base()
    (POSITIVE | MUTANTS)[name](p)
    p.plan_id = name
    return p


def status(rule_id: str, name: str) -> Status:
    return check(scenario(name), NET, CATALOGUE, REGISTER).by_rule()[rule_id].status


def expected(rule: cat.Rule) -> Status:
    cap = Severity(REGISTER.value("rules.unverified_max_severity"))
    return Status.FAIL if rule.effective_severity(cap) is Severity.FAIL else Status.WARN


POS_CASES = [(r.rule_id, n) for r in CATALOGUE.rules for n in r.tests.positive]
NEG_CASES = [(r.rule_id, n) for r in CATALOGUE.rules for n in r.tests.negative]


def test_the_committed_catalogue_passes_the_lint() -> None:
    assert cat.lint(CATALOGUE, REGISTER) == []


def test_every_rule_names_existing_positive_and_negative_scenarios() -> None:
    for r in CATALOGUE.rules:
        assert r.tests.positive and r.tests.negative, r.rule_id
        assert set(r.tests.positive) <= POSITIVE.keys(), r.rule_id
        assert set(r.tests.negative) <= MUTANTS.keys(), r.rule_id
    assert {n for _, n in NEG_CASES} == MUTANTS.keys()  # no orphan mutant


@pytest.mark.parametrize(("rule_id", "name"), POS_CASES)
def test_rule_passes_its_positive_scenarios(rule_id: str, name: str) -> None:
    assert status(rule_id, name) is Status.PASS


@pytest.mark.parametrize(("rule_id", "name"), NEG_CASES)
def test_rule_flags_its_mutant_at_its_effective_severity(rule_id: str, name: str) -> None:
    assert status(rule_id, name) is expected(CATALOGUE.rule(rule_id))


def test_mutation_catch_rate_is_100_percent() -> None:
    caught = sum(status(rule_id, name) is not Status.PASS for rule_id, name in NEG_CASES)
    assert caught / len(NEG_CASES) == 1.0


def test_no_unverified_rule_ever_returns_fail() -> None:
    unverified = [r for r in CATALOGUE.rules if r.unverified]
    assert unverified and any(r.severity is Severity.FAIL for r in unverified)
    for name in POSITIVE.keys() | MUTANTS.keys():
        report = check(scenario(name), NET, CATALOGUE, REGISTER)
        for r in unverified:
            assert report.by_rule()[r.rule_id].status is not Status.FAIL, (r.rule_id, name)


def test_the_unverified_cap_comes_from_the_register() -> None:
    rule = CATALOGUE.rule("VER-BLK-04")
    assert rule.severity is Severity.FAIL and rule.unverified
    assert rule.effective_severity(Severity.WARN) is Severity.WARN
    assert CATALOGUE.rule("VER-BLK-01").effective_severity(Severity.WARN) is Severity.FAIL


def test_the_base_plan_is_verified_with_every_rule_listed() -> None:
    report = check(base(), NET, CATALOGUE, REGISTER)
    assert report.overall is Overall.PASS
    assert report.badge() == f"Verified ({len(CATALOGUE.rules)}/{len(CATALOGUE.rules)} rules)"
    assert all(r.citation.reference for r in report.results)


def test_a_proceed_without_line_clear_action_fails_g2_with_ver_aut_01() -> None:
    """FR-VER-10 acceptance."""
    report = check(scenario("aut01_proceed_without_line_clear"), NET, CATALOGUE, REGISTER)
    assert report.overall is Overall.FAIL
    result = report.by_rule()["VER-AUT-01"]
    assert result.status is Status.FAIL and result.violations[0].trains == ["T1"]
    assert "VER-AUT-01" in report.badge()


def test_a_violation_names_trains_time_and_citation() -> None:
    result = check(scenario("blk01_two_in_section"), NET, CATALOGUE, REGISTER).by_rule()["VER-BLK-01"]
    assert result.violations[0].trains == ["T1", "T3"] and result.violations[0].time_s == 300
    assert result.citation.type == "GSR" and "GR 8.01" in result.citation.reference


def test_warnings_allow_acceptance_with_warn_badge() -> None:
    report = check(scenario("buf01_thin_buffer"), NET, CATALOGUE, REGISTER)
    assert report.overall is Overall.PASS_WITH_WARN
    assert report.badge().startswith("Verified with warnings")


def test_a_rule_without_its_context_is_not_applicable() -> None:
    p = base()
    p.maintenance, p.frozen = [], []
    results = check(p, NET, CATALOGUE, REGISTER).by_rule()
    assert results["VER-MNT-01"].status is Status.NOT_APPLICABLE
    assert results["VER-FRZ-01"].status is Status.NOT_APPLICABLE


def test_a_predicate_error_is_an_error_and_fails_the_plan() -> None:
    p = base()
    p.maintenance[0].block_id = "X-Y"
    report = check(p, NET, CATALOGUE, REGISTER)
    assert report.by_rule()["VER-MNT-01"].status is Status.ERROR
    assert report.overall is Overall.FAIL


def test_a_rule_reading_an_undeclared_parameter_errors() -> None:
    rule = CATALOGUE.rule("VER-BLK-01").model_copy(update={"parameters": []})
    one = cat.Catalogue(catalogue_version="t", spec_version="1.6", rules=[rule])
    result = check(base(), NET, one, REGISTER).by_rule()["VER-BLK-01"]
    assert result.status is Status.ERROR and "does not declare" in (result.error or "")


def test_the_report_round_trips_as_json_with_versions() -> None:
    """FR-VER-11: results persist per recommendation with rule id, status, details and versions."""
    report = check(scenario("xng02_short_loop"), NET, CATALOGUE, REGISTER)
    again = type(report).model_validate_json(report.model_dump_json())
    assert again == report
    assert again.catalogue_version == CATALOGUE.catalogue_version and again.register_version


def test_the_lint_rejects_a_rule_without_a_source_or_with_an_unknown_parameter() -> None:
    raw = CATALOGUE.model_dump(mode="json")
    del raw["rules"][0]["source"]
    with pytest.raises(Exception, match="source"):
        cat.Catalogue.model_validate(raw)
    rule = CATALOGUE.rules[0].model_copy(update={"parameters": ["ver.no_such_value"]})
    bad = cat.Catalogue(catalogue_version="t", spec_version="1.6", rules=[rule])
    assert any("not in the register" in p for p in cat.lint(bad, REGISTER))
    with pytest.raises(cat.CatalogueError):
        cat.resolve("python:os.system")


def test_the_published_network_loads_without_the_etl() -> None:
    net = network.load(REGISTER.value("network.version"))
    assert len(net.stations) == 18 and net.connections and net.sub_blocks
    assert all(t.capacity >= 1 for t in net.tracks.values())
