"""The versioned rule catalogue (spec §9.2; FR-VER-03, FR-VER-04; OD-05).

Rules are declarative YAML entries in ``config/rule_catalogue.yaml`` with a Python predicate. Every
rule cites its source (type, reference, verification status) and names the register parameters it
reads. ``lint`` is the CI gate of FR-VER-04: it fails any rule without a usable source, with an
unknown parameter or predicate, or without at least one positive and one negative test.

OD-05: a rule whose fact is unverified (``verification_status: U``) reports at most
``rules.unverified_max_severity`` (WARN), whatever its declared severity.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError

from raildss_params import Register
from raildss_params.register import UnknownParameterError
from raildss_params.register import default_path as register_path
from raildss_verifier.model import Severity

DEFAULT_PATH = register_path().parent / "rule_catalogue.yaml"
PREDICATE_PREFIX = "python:raildss_verifier.rules."
UNVERIFIED = "U"
SourceType = Literal[
    "GSR",
    "SR",
    "SWR",
    "BWM",
    "WTT",
    "COA_MASTER",
    "OPERATING_MANUAL",
    "RAILDSS_POLICY",
    "DEMO_ASSUMPTION",
]
Category = Literal[
    "block", "headway", "crossing", "platform", "speed", "blocks", "crew", "plan", "authority", "data"
]


class CatalogueError(ValueError):
    pass


class Source(BaseModel):
    type: SourceType
    reference: str = Field(min_length=1)
    clause_text_ref: str | None = None
    verification_status: Literal["V", "U", "SA", "EST"]


class RuleTests(BaseModel):
    positive: list[str] = Field(min_length=1)
    negative: list[str] = Field(min_length=1)


class Rule(BaseModel):
    rule_id: str = Field(pattern=r"^VER-[A-Z]{3}-\d{2}$")
    title: str = Field(min_length=1)
    category: Category
    severity: Severity
    applies_to: dict[str, Any] = Field(default_factory=dict)
    predicate: str = Field(pattern=r"^python:raildss_verifier\.rules\.[a-z_]+\.[a-z_]+$")
    parameters: list[str] = Field(default_factory=list)
    source: Source
    owner: str = Field(min_length=1)
    tests: RuleTests
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    notes: str | None = None

    @property
    def unverified(self) -> bool:
        return self.source.verification_status == UNVERIFIED

    def effective_severity(self, unverified_cap: Severity) -> Severity:
        """The declared severity, capped for [U] rules (OD-05)."""
        if self.unverified and self.severity.rank > unverified_cap.rank:
            return unverified_cap
        return self.severity


class Catalogue(BaseModel):
    catalogue_version: str = Field(min_length=1)
    spec_version: str
    rules: list[Rule]

    def rule(self, rule_id: str) -> Rule:
        for r in self.rules:
            if r.rule_id == rule_id:
                return r
        raise KeyError(rule_id)


def resolve(predicate: str) -> Callable[..., Any]:
    """Import ``python:raildss_verifier.rules.<module>.<function>`` (only the verifier's own rules)."""
    if not predicate.startswith(PREDICATE_PREFIX):
        raise CatalogueError(f"predicate {predicate!r} is outside {PREDICATE_PREFIX}")
    module, _, name = predicate.removeprefix("python:").rpartition(".")
    fn = getattr(importlib.import_module(module), name, None)
    if not callable(fn):
        raise CatalogueError(f"predicate {predicate!r} does not resolve to a function")
    return fn  # type: ignore[no-any-return]


def load(path: Path | str | None = None) -> Catalogue:
    doc = yaml.safe_load(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
    try:
        return Catalogue.model_validate(doc)
    except ValidationError as exc:
        raise CatalogueError(str(exc)) from exc


def lint(catalogue: Catalogue, register: Register) -> list[str]:
    """FR-VER-04 problems: duplicate ids, unresolvable predicates, parameters not in the register."""
    problems: list[str] = []
    seen: set[str] = set()
    for rule in catalogue.rules:
        if rule.rule_id in seen:
            problems.append(f"{rule.rule_id}: duplicate rule id")
        seen.add(rule.rule_id)
        try:
            resolve(rule.predicate)
        except (CatalogueError, ImportError) as exc:
            problems.append(f"{rule.rule_id}: {exc}")
        for key in rule.parameters:
            try:
                register.get(key)
            except UnknownParameterError:
                problems.append(f"{rule.rule_id}: parameter {key!r} is not in the register")
    try:
        Severity(register.value("rules.unverified_max_severity"))
    except (UnknownParameterError, ValueError) as exc:
        problems.append(f"rules.unverified_max_severity: {exc}")
    return problems
