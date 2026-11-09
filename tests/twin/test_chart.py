"""Control-chart export for the web UI (FR-UI-02, FR-UI-03; issue #86)."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from raildss_twin import chart, run

DAY = dt.date(2025, 8, 20)
COMMITTED = Path(__file__).resolve().parents[2] / "web" / "public" / "data" / f"{DAY.isoformat()}.json"


@pytest.fixture(scope="module")
def data() -> dict[str, Any]:
    return chart.chart_data(run.load_world(), DAY)


def test_a_full_real_day_exports_every_train_with_stations_and_loops(data: dict[str, Any]) -> None:
    assert data["format"] == chart.FORMAT and data["completed"]
    assert len(data["trains"]) > 150
    block = [s for s in data["stations"] if not s["halt"]]
    assert len(data["stations"]) == 22 and len(block) == 18
    assert all(s["loops"] >= 1 for s in block)
    kms = [s["km"] for s in data["stations"]]
    assert kms == sorted(kms)


def test_lines_run_forward_in_time_and_passenger_trains_have_a_booked_line(data: dict[str, Any]) -> None:
    for t in data["trains"]:
        times = [p[0] for p in t["actual"]]
        assert times and times == sorted(times), t["id"]
        assert bool(t["booked"]) == (t["kind"] == "passenger"), t["id"]


def test_the_demo_preview_changes_some_trains(data: dict[str, Any]) -> None:
    preview = data["preview"]
    assert preview["demo"] and len(preview["options"]) == 1
    option = preview["options"][0]
    assert option["trains"] and option["orders"]
    actual = {t["id"]: t["actual"] for t in data["trains"]}
    assert all(pts != actual[rid] for rid, pts in option["trains"].items())


def test_the_committed_ui_data_is_current(data: dict[str, Any]) -> None:
    """web/public/data is regenerated with `python -m raildss_twin chart --date 2025-08-20`."""
    committed = json.loads(COMMITTED.read_text(encoding="utf-8"))
    assert committed == json.loads(json.dumps(data))
