"""Smoke tests: every RailDSS package imports and exposes a version (issue #1)."""

from __future__ import annotations

import importlib

import pytest

PACKAGES = [
    "raildss_common",
    "raildss_schema",
    "raildss_params",
    "raildss_etl",
    "raildss_twin",
    "raildss_optimiser",
    "raildss_verifier",
]


@pytest.mark.parametrize("name", PACKAGES)
def test_package_imports(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__version__ == "0.1.0"
    assert module.__doc__
