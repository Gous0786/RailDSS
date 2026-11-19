"""The tamper-evident audit log and its queries (issue #85; FR-AUD-02, -05, -07)."""

from __future__ import annotations

import datetime as dt
import itertools
import time
from pathlib import Path

import pytest

from raildss_audit.__main__ import main
from raildss_audit.chain import GENESIS, AuditLog, pseudonym

T0 = dt.datetime(2025, 8, 20, 2, 30, tzinfo=dt.UTC)


def fill(log: AuditLog, n: int = 12, *, start: dt.datetime = T0) -> None:
    for i in range(n):
        t = start + dt.timedelta(minutes=i)
        log.append(
            "alert" if i % 3 == 0 else "recommendation" if i % 3 == 1 else "decision",
            {"n": i, "detail": f"event {i}"},
            t,
            actor="dss" if i % 3 == 0 else pseudonym("ravi", log.salt),
            refs={"trains": [f"T{i % 4}"], "rec_id": f"REC-{i:04d}", "incident": f"INC-{i % 2}"},
        )


def test_records_are_chained_and_the_chain_verifies(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log)
    rows = log.records()
    assert rows[0].prev == GENESIS
    assert all(b.prev == a.hash for a, b in itertools.pairwise(rows))
    result = log.verify()
    assert result.ok and result.records == 12
    assert AuditLog(tmp_path, "KTV-PSA").verify().ok  # and after a reload from disk


def test_a_day_continues_the_chain_of_the_day_before(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log, 3)
    fill(log, 3, start=T0 + dt.timedelta(days=1))
    days = log.days()
    assert len(days) == 2
    assert log.records(days[1])[0].prev == log.records(days[0])[-1].hash
    assert log.verify().ok


@pytest.mark.parametrize("what", ["payload", "actor", "time", "delete", "swap"])
def test_tampering_with_one_record_breaks_the_chain(tmp_path: Path, what: str) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log)
    log.anchor(log.days()[0])
    path = next((tmp_path / "KTV-PSA").glob("*.jsonl"))
    lines = path.read_text(encoding="utf-8").splitlines()
    if what == "payload":
        lines[5] = lines[5].replace('"n":5', '"n":6')
    elif what == "actor":
        lines[4] = lines[4].replace("ctl-", "ctl-x")
    elif what == "time":
        lines[2] = lines[2].replace("T02:32", "T02:33")
    elif what == "delete":
        del lines[7]
    else:
        lines[3], lines[4] = lines[4], lines[3]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = AuditLog(tmp_path, "KTV-PSA").verify()
    assert not result.ok and result.problems


def test_truncating_the_tail_is_caught_by_the_daily_anchor(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log)
    anchor = log.anchor(log.days()[0])
    assert anchor["count"] == 12
    path = next((tmp_path / "KTV-PSA").glob("*.jsonl"))
    path.write_text("\n".join(path.read_text(encoding="utf-8").splitlines()[:-2]) + "\n", encoding="utf-8")
    result = AuditLog(tmp_path, "KTV-PSA").verify()
    assert not result.ok and any("anchor" in p for p in result.problems)


def test_queries_by_train_time_controller_incident_and_recommendation(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log)
    assert {r.refs["trains"][0] for r in log.query(train="T1")} == {"T1"}
    assert len(log.query(kind="alert")) == 4
    window = log.query(since=T0 + dt.timedelta(minutes=3), until=T0 + dt.timedelta(minutes=5))
    assert [r.payload["n"] for r in window] == [3, 4, 5]
    mine = log.query(controller="ravi")  # a name is pseudonymised for the lookup
    assert mine and all(r.actor == pseudonym("ravi", log.salt) for r in mine)
    assert log.query(controller=pseudonym("ravi", log.salt)) == mine
    assert {r.refs["incident"] for r in log.query(incident="INC-1")} == {"INC-1"}
    assert [r.refs["rec_id"] for r in log.query(rec_id="REC-0004")] == ["REC-0004"]
    assert "ravi" not in "".join(r.model_dump_json() for r in log.records())  # only the pseudonym is stored


def test_configuration_changes_carry_before_after_and_approvers(tmp_path: Path) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    rec = log.config_change(
        "policy table", "policy.weights.freight_other", 1, 2, ["asha", "dev"], T0, by="admin1"
    )
    assert rec.kind == "config_change"
    assert rec.payload["before"] == 1 and rec.payload["after"] == 2
    assert rec.payload["approvers"] == [pseudonym("asha", log.salt), pseudonym("dev", log.salt)]
    assert rec.actor == pseudonym("admin1", log.salt)
    assert log.query(kind="config_change")[0].refs["config_key"] == "policy.weights.freight_other"


def test_a_query_over_30_days_of_audit_data_takes_under_two_seconds(tmp_path: Path) -> None:
    log = AuditLog(None, "KTV-PSA")
    for day in range(30):
        fill(log, 2000, start=T0 + dt.timedelta(days=day))
    t0 = time.perf_counter()
    rows = log.query(train="T2", since=T0 + dt.timedelta(days=5), until=T0 + dt.timedelta(days=25))
    assert rows and time.perf_counter() - t0 < 2.0
    assert len(log) == 60000


def test_cli_verify_query_export(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    log = AuditLog(tmp_path, "KTV-PSA")
    fill(log, 6)
    assert main(["verify", "--dir", str(tmp_path)]) == 0
    assert main(["query", "--dir", str(tmp_path), "--kind", "alert"]) == 0
    assert "2 records" in capsys.readouterr().out
    out = tmp_path / "audit.json"
    assert main(["export", "--dir", str(tmp_path), "--out", str(out)]) == 0
    assert '"verification"' in out.read_text(encoding="utf-8")
    path = next((tmp_path / "KTV-PSA").glob("*.jsonl"))
    path.write_text(path.read_text(encoding="utf-8").replace('"n":2', '"n":9'), encoding="utf-8")
    assert main(["verify", "--dir", str(tmp_path)]) == 1
