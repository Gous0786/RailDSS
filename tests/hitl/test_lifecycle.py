"""The recommendation lifecycle (FR-HITL-01, -02, -07; issue #82).

The exhaustive test explores the implementation in the bounded world of the TLA+ model and checks
the model's invariants in every reachable state; it runs everywhere (TLC runs in CI only).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from raildss_hitl.lifecycle import Lifecycle, Status, TransitionError
from tests.hitl import model


@pytest.mark.parametrize("cfg", model.CONFIGS, ids=lambda p: p.stem)
def test_every_reachable_state_satisfies_the_model_invariants(cfg: Path) -> None:
    b = model.bounds(cfg)
    graph = model.reachable(b)
    assert len(graph) > 50
    bad = {s: v for s in graph if (v := model.violations(s, b))}
    assert not bad, next(iter(bad.items()))


@pytest.mark.parametrize("cfg", model.CONFIGS, ids=lambda p: p.stem)
def test_every_model_action_is_exercised(cfg: Path) -> None:
    b = model.bounds(cfg)
    labels = {label for edges in model.reachable(b).values() for label, _ in edges}
    tla = (model.SPECS / "HitlLifecycle.tla").read_text(encoding="utf-8")
    declared = set(re.findall(r"\\E r \\in Recs : (\w+)\(r\)", tla))
    assert declared | {"Tick", "StateChange"} == labels


@pytest.mark.parametrize("cfg", model.CONFIGS, ids=lambda p: p.stem)
def test_a_stale_accepted_plan_is_superseded_at_the_pre_execution_check(cfg: Path) -> None:
    b = model.bounds(cfg)
    for s in model.reachable(b):
        for i, r in enumerate(b.recs):
            if s[0][i] in ("ACCEPTED", "PRE_EXEC_CHECK") and s[1][i] != s[5]:
                lc = model.from_state(s, b)
                if lc.recs[r].status is Status.ACCEPTED:
                    lc.pre_exec_check(r)
                assert lc.pre_exec_result(r) is Status.SUPERSEDED


def presented(lc: Lifecycle, rec_id: str, context: str = "CPP precedence", valid_until: float = 600) -> None:
    lc.propose(rec_id, context, {"CPP-SGDM"}, valid_until)
    lc.verification_result(rec_id, True)


def test_a_recommendation_expires_at_valid_until_and_cannot_be_accepted() -> None:
    lc = Lifecycle()
    presented(lc, "R-1", valid_until=600)
    lc.advance(599)
    assert lc.expire_due() == []
    lc.advance(600)
    with pytest.raises(TransitionError):
        lc.accept("R-1")
    assert lc.expire_due() == ["R-1"]
    assert lc.recs["R-1"].status is Status.EXPIRED


def test_a_new_recommendation_for_the_same_context_supersedes_the_old_one() -> None:
    lc = Lifecycle()
    presented(lc, "R-1")
    presented(lc, "R-2")
    assert lc.recs["R-1"].status is Status.SUPERSEDED
    with pytest.raises(TransitionError):
        lc.accept("R-1")
    presented(lc, "R-3", context="SGDM crossing")
    assert lc.recs["R-2"].status is Status.PRESENTED  # another context is untouched


def test_no_recommendation_is_presented_while_one_of_its_context_is_committed() -> None:
    lc = Lifecycle()
    presented(lc, "R-1")
    lc.accept("R-1")
    lc.propose("R-2", "CPP precedence", {"CPP-SGDM"}, 600)
    with pytest.raises(TransitionError):
        lc.verification_result("R-2", True)


def test_accept_to_execution_and_a_state_change_diverges_orders_in_force() -> None:
    lc = Lifecycle()
    presented(lc, "R-1")
    lc.accept("R-1")
    lc.pre_exec_check("R-1")
    assert lc.pre_exec_result("R-1") is Status.ORDER_ISSUED
    lc.acknowledged("R-1")
    lc.start("R-1")
    lc.state_changed()
    assert lc.recs["R-1"].status is Status.DIVERGED
    assert lc.recs["R-1"].history[-1][1] is Status.DIVERGED


def test_a_second_plan_on_a_held_block_is_superseded_at_the_pre_execution_check() -> None:
    lc = Lifecycle()
    for rid, ctx in (("R-1", "CPP precedence"), ("R-2", "SGDM crossing")):
        presented(lc, rid, context=ctx)
        lc.accept(rid)
        lc.pre_exec_check(rid)
    assert lc.pre_exec_result("R-1") is Status.ORDER_ISSUED
    assert lc.pre_exec_result("R-2") is Status.SUPERSEDED


def test_a_declined_order_is_replanned_and_needs_a_new_decision() -> None:
    lc = Lifecycle()
    presented(lc, "R-1")
    lc.accept("R-1")
    lc.pre_exec_check("R-1")
    lc.pre_exec_result("R-1")
    lc.declined("R-1")
    lc.replan("R-1", valid_until=900)
    rec = lc.recs["R-1"]
    assert rec.status is Status.PRESENTED and not rec.decided and rec.valid_until == 900


def test_reject_needs_a_reason_code_and_a_failed_verification_is_never_presented() -> None:
    lc = Lifecycle()
    presented(lc, "R-1")
    with pytest.raises(TransitionError):
        lc.reject("R-1", "")
    lc.reject("R-1", "local_knowledge")
    assert lc.recs["R-1"].reason == "local_knowledge"
    lc.propose("R-2", "CPP precedence", {"CPP-SGDM"}, 600)
    lc.verification_result("R-2", False)
    assert lc.recs["R-2"].status is Status.REJECTED_BY_VERIFIER
    with pytest.raises(TransitionError):
        lc.accept("R-2")
