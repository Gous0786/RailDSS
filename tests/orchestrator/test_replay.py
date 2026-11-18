"""The M1 demo command: closed-loop replay of a real day against BL-0 (issue #90, FR-SCN-05/08)."""

from __future__ import annotations

import json
from pathlib import Path

from raildss_orchestrator.__main__ import main


def test_replay_writes_the_dss_vs_bl0_report(tmp_path: Path) -> None:
    report, out = tmp_path / "replay.md", tmp_path / "replay.json"
    assert main(["replay", "--from-h", "8", "--to-h", "8.0333", "--tick-s", "120",
                 "--report", str(report), "--out", str(out)]) == 0  # fmt: skip
    text = report.read_text(encoding="utf-8")
    assert "# Closed-loop replay of 2025-08-20" in text and "options by greedy" in text
    assert "| KPI | DSS | BL-0 |" in text and "A5_weighted_delay_train_min" in text
    assert "### DSS minus BL-0" in text
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["accepted"] > 0 and doc["orders"] > 0  # greedy recommendations executed in the twin
    assert set(doc["kpis"]["arms"]) == {"DSS", "BL-0"}
    # every option that was released came from a plan that passed G0-G3
    for cycle in doc["cycles"]:
        verified = {c["name"] for c in cycle["candidates"] if c["verified"]}
        assert all(r["candidate"] in verified for r in cycle["released"])
