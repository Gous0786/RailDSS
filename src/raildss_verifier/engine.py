"""G2 independent rule checker (spec §9.3-9.4; FR-VER-03).

``check`` evaluates every catalogue rule on a plan and returns PASS / WARN / FAIL per rule with the
violating trains, times and the rule's citation. A predicate that raises is an ERROR, which counts
as FAIL overall (spec §9.4). A predicate returning ``None`` is NOT_APPLICABLE.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from raildss_params import Register, load
from raildss_verifier import catalogue as cat
from raildss_verifier.model import (
    CandidatePlan,
    Citation,
    Overall,
    Report,
    RuleResult,
    Severity,
    Status,
    Violation,
)
from raildss_verifier.network import Network

Predicate = Callable[["Context"], list[Violation] | None]


@dataclass
class Context:
    """What a predicate sees. ``param`` only returns parameters the rule declares (FR-VER-04)."""

    plan: CandidatePlan
    net: Network
    rule: cat.Rule
    register: Register

    def param(self, key: str) -> Any:
        if key not in self.rule.parameters:
            raise KeyError(f"{self.rule.rule_id} reads {key!r} but does not declare it")
        return self.register.value(key)


def _status(violations: list[Violation], severity: Severity) -> Status:
    if not violations or severity is Severity.INFO:
        return Status.PASS
    return Status.FAIL if severity is Severity.FAIL else Status.WARN


def evaluate(rule: cat.Rule, plan: CandidatePlan, net: Network, register: Register) -> RuleResult:
    cap = Severity(register.value("rules.unverified_max_severity"))
    effective = rule.effective_severity(cap)
    result = RuleResult(
        rule_id=rule.rule_id,
        title=rule.title,
        status=Status.PASS,
        declared_severity=rule.severity,
        effective_severity=effective,
        citation=Citation(
            type=rule.source.type,
            reference=rule.source.reference,
            verification_status=rule.source.verification_status,
        ),
    )
    try:
        found = cat.resolve(rule.predicate)(Context(plan, net, rule, register))
    except Exception as exc:  # any predicate failure is an ERROR result, never a silent pass
        result.status, result.error = Status.ERROR, f"{type(exc).__name__}: {exc}"
        return result
    if found is None:
        result.status = Status.NOT_APPLICABLE
        return result
    result.violations = sorted(found, key=lambda v: (v.time_s, v.trains))
    result.status = _status(result.violations, effective)
    return result


def overall(results: list[RuleResult]) -> Overall:
    statuses = {r.status for r in results}
    if statuses & {Status.FAIL, Status.ERROR}:
        return Overall.FAIL
    return Overall.PASS_WITH_WARN if Status.WARN in statuses else Overall.PASS


def check(
    plan: CandidatePlan,
    net: Network,
    catalogue: cat.Catalogue | None = None,
    register: Register | None = None,
) -> Report:
    """Run every rule of ``catalogue`` (default: the committed catalogue) on ``plan``."""
    catalogue = catalogue or cat.load()
    register = register or load()
    results = [evaluate(rule, plan, net, register) for rule in catalogue.rules]
    return Report(
        plan_id=plan.plan_id,
        network_version=net.version,
        catalogue_version=catalogue.catalogue_version,
        register_version=register.version,
        overall=overall(results),
        results=results,
    )
