"""Parameter register: model, lint rules and loader (spec C-05, FR-DATA-01/08, DQ-13)."""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict

Status = Literal["V", "DATA", "EST", "U", "SA", "DUMMY"]
STATUSES: tuple[str, ...] = ("V", "DATA", "EST", "U", "SA", "DUMMY")

KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[A-Za-z0-9_]+)+$")
# TABLE.COLUMN as used in the KTV-PSA dataset (table names may contain spaces, parentheses, hyphens).
TABLE_COLUMN_RE = re.compile(r"[A-Za-z][\w ()\-]*\.[A-Z][A-Za-z_ ]*")
REPORT_SECTION_RE = re.compile(r"\bR0[1-6]\b|§")
URL_RE = re.compile(r"^https?://\S+$")

ENV_VAR = "RAILDSS_PARAMS"


class RegisterError(ValueError):
    """The register file is invalid (lint problems are listed in the message)."""


class UnknownParameterError(KeyError):
    """A requested key is not in the register."""


class Bounds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    low: Any
    high: Any


class Parameter(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str
    value: Any = None
    value_ref: str | None = None
    unit: str | None
    source: str
    status: Status
    owner: str
    bounds: Bounds | None = None
    provenance: str | None = None
    valid_from: dt.date
    notes: str | None = None

    @property
    def is_reference(self) -> bool:
        """True when the value lives per record in the dataset (``value_ref``)."""
        return self.value_ref is not None


class Register(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    register_version: str
    spec_version: str
    content_sha256: str
    parameters: dict[str, Parameter]

    @property
    def version(self) -> str:
        """Version stamp written into outputs, e.g. ``2026.11.1+3f2a9c1b0d4e``."""
        return f"{self.register_version}+{self.content_sha256[:12]}"

    def get(self, key: str) -> Parameter:
        try:
            return self.parameters[key]
        except KeyError:
            raise UnknownParameterError(f"unknown parameter key: {key!r}") from None

    def value(self, key: str) -> Any:
        p = self.get(key)
        if p.is_reference:
            raise RegisterError(f"{key!r} is per record in the dataset ({p.value_ref}); read it from the ETL")
        return p.value

    def by_status(self, status: str) -> list[Parameter]:
        return [p for p in self.parameters.values() if p.status == status]


REQUIRED = ("key", "unit", "source", "status", "owner", "valid_from")
ALLOWED = {*REQUIRED, "value", "value_ref", "bounds", "provenance", "notes"}


def lint(doc: Any) -> list[str]:
    """Return every problem in a parsed register document (empty list = clean)."""
    problems: list[str] = []
    if not isinstance(doc, dict):
        return ["register must be a mapping"]
    for top in ("register_version", "spec_version", "parameters"):
        if top not in doc:
            problems.append(f"missing top-level field {top!r}")
    entries = doc.get("parameters") or []
    if not isinstance(entries, list):
        return [*problems, "'parameters' must be a list"]
    seen: set[str] = set()
    for i, e in enumerate(entries):
        where = f"parameters[{i}]"
        if not isinstance(e, dict):
            problems.append(f"{where}: entry must be a mapping")
            continue
        key = e.get("key")
        where = f"{key or where}"
        for f in REQUIRED:
            if f not in e or (f != "unit" and e[f] in (None, "")):
                problems.append(f"{where}: missing {f!r}")
        for f in e.keys() - ALLOWED:
            problems.append(f"{where}: unknown field {f!r}")
        if isinstance(key, str):
            if not KEY_RE.match(key):
                problems.append(f"{where}: key must be dotted lower-case, e.g. 'block.vmax_kmh'")
            if key in seen:
                problems.append(f"{where}: duplicate key")
            seen.add(key)
        has_value, has_ref = "value" in e, e.get("value_ref") is not None
        if has_value == has_ref:
            problems.append(f"{where}: exactly one of 'value' or 'value_ref' is required")
        if has_ref and not TABLE_COLUMN_RE.fullmatch(str(e["value_ref"])):
            problems.append(f"{where}: value_ref must be TABLE.COLUMN")
        status, source = e.get("status"), str(e.get("source") or "")
        if status is not None and status not in STATUSES:
            problems.append(f"{where}: status must be one of {', '.join(STATUSES)}")
        if status == "DATA" and not (TABLE_COLUMN_RE.search(source) or REPORT_SECTION_RE.search(source)):
            problems.append(f"{where}: DATA source must cite TABLE.COLUMN or a report section")
        if status == "EST":
            b = e.get("bounds")
            if not (isinstance(b, dict) and "low" in b and "high" in b):
                problems.append(f"{where}: EST entries need bounds with 'low' and 'high'")
        if status == "DUMMY":
            prov = str(e.get("provenance") or "")
            if not (prov == "placeholder" or URL_RE.match(prov)):
                problems.append(f"{where}: DUMMY entries need provenance (URL or 'placeholder')")
    return problems


def default_path() -> Path:
    """``$RAILDSS_PARAMS`` if set, else ``config/parameters.yaml`` at the repository root."""
    env = os.environ.get(ENV_VAR)
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[2] / "config" / "parameters.yaml"


def load(path: Path | str | None = None) -> Register:
    """Load and lint a register. Raises :class:`RegisterError` listing every problem."""
    p = Path(path) if path is not None else default_path()
    raw = p.read_bytes()
    doc = yaml.safe_load(raw)
    problems = lint(doc)
    if problems:
        raise RegisterError(f"{p}: {len(problems)} problem(s):\n  " + "\n  ".join(problems))
    params = {e["key"]: Parameter.model_validate(e) for e in doc["parameters"]}
    return Register(
        register_version=str(doc["register_version"]),
        spec_version=str(doc["spec_version"]),
        content_sha256=hashlib.sha256(raw).hexdigest(),
        parameters=params,
    )


@lru_cache(maxsize=1)
def _default_register() -> Register:
    return load()


def get(key: str) -> Parameter:
    """Return a parameter from the default register; unknown keys raise."""
    return _default_register().get(key)


def value(key: str) -> Any:
    """Return a parameter value from the default register; unknown keys raise."""
    return _default_register().value(key)
