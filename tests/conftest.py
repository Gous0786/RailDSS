"""Shared pytest configuration (issue #181: cut CI test time).

* **The ``slow`` marker.** Tests that run whole-day twin simulations, the decision loop, scenarios,
  calibration or the full ETL are marked ``slow`` here, by path, in one place. CI runs the fast suite
  (``-m "not slow"``) in the main job and the slow tests in parallel jobs (``-m slow``); every test runs in
  exactly one of them. Locally: ``uv run pytest -n auto`` for everything, ``-m "not slow"`` for a quick run.
* **One ``World`` per process.** ``run.load_world()`` reads the published network and builds the layout and
  the running-time model (seconds, and about forty test modules ask for it). Tests only read a world (the
  one that changes the register copies it with ``dataclasses.replace``), so a call without arguments
  returns the same object for the whole pytest process (each xdist worker has its own).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

SLOW_PATHS = (
    "tests/twin/",
    "tests/orchestrator/",
    "tests/dss/test_state.py",
    "tests/dss/test_kpi.py",
    "tests/dss/test_predict.py",
    "tests/dss/test_predict_degraded.py",
    "tests/dss/test_conflicts.py",
    "tests/etl/test_publish.py",
    "tests/etl/test_boundaries.py",
    "tests/etl/test_dq.py",
    "tests/etl/test_kpi_reference.py",
    "tests/optimiser/test_greedy.py",
    "tests/optimiser/test_cpsat.py",
    "tests/verifier/test_auditor.py",
)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    for item in items:
        path = item.path.relative_to(config.rootpath).as_posix()
        if path.startswith(SLOW_PATHS):
            item.add_marker(pytest.mark.slow)


@pytest.fixture(scope="session", autouse=True)
def _one_world_per_process() -> Iterator[None]:
    from raildss_twin import run

    original = run.load_world
    shared: list[Any] = []

    def load_world(*args: Any, **kwargs: Any) -> Any:
        if args or kwargs:
            return original(*args, **kwargs)
        if not shared:
            shared.append(original())
        return shared[0]

    run.load_world = load_world
    yield
    run.load_world = original
