"""Dummy-data catalogue checks (issue #24 acceptance criteria; spec C-03, C-14)."""

from __future__ import annotations

import math
import re
from pathlib import Path

from raildss_params import load, value

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = ROOT / "docs" / "data" / "dummy-data-catalogue.md"

ROW_RE = re.compile(r"^\| (DC-\d{2}) \|")
KEY_STATUS_RE = re.compile(r"`([a-z][\w.]*(?:\.\w+)+)` \((V|DATA|EST|U|SA|DUMMY)(?:/\w+)*\)")
DATE_RE = re.compile(r"\| (\d{4}-\d{2}-\d{2}) \|")
LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)")
# C-03: NTES and running-status sites must never be a data source.
FORBIDDEN = (
    "ntes",
    "enquiry.indianrail",
    "runningstatus",
    "etrain",
    "erail",
    "railyatri",
    "confirmtkt",
    "trainspnr",
)


def _rows() -> list[tuple[str, str]]:
    rows = []
    for line in CATALOGUE.read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m:
            rows.append((m.group(1), line))
    return rows


def test_catalogue_has_rows_with_check_dates() -> None:
    rows = _rows()
    assert len(rows) >= 14
    for row_id, line in rows:
        assert DATE_RE.search(line), f"{row_id}: missing check date"


def test_catalogue_keys_match_register_status() -> None:
    reg = load()
    for row_id, line in _rows():
        for key, status in KEY_STATUS_RE.findall(line):
            assert key in reg.parameters, f"{row_id}: {key} not in register"
            actual = reg.get(key).status
            assert actual == status, f"{row_id}: {key} is {actual}, catalogue says {status}"


def test_every_dummy_value_is_catalogued() -> None:
    text = CATALOGUE.read_text(encoding="utf-8")
    missing = [p.key for p in load().by_status("DUMMY") if f"`{p.key}` (DUMMY)" not in text]
    assert missing == [], f"DUMMY register entries not in the catalogue: {missing}"


def test_no_running_status_sources() -> None:
    urls = LINK_RE.findall(CATALOGUE.read_text(encoding="utf-8"))
    urls += [p.provenance or "" for p in load().parameters.values()]
    bad = [u for u in urls if any(f in u.lower() for f in FORBIDDEN)]
    assert bad == [], f"forbidden sources (C-03): {bad}"


def _share_within(p_on: float, mu: float, sigma: float, limit_min: float) -> float:
    tail = 0.5 * (1 + math.erf((math.log(limit_min) - mu) / (sigma * math.sqrt(2))))
    return p_on + (1 - p_on) * tail


def test_entry_profile_meets_ecor_adjusted_target() -> None:
    prof = value("delay.passenger.entry_profile")
    tail, band = prof["tail_lognormal"], prof["target_on_time_15min_share"]
    limit = value("kpi.punctuality_tolerance_min")
    share = _share_within(prof["p_on_time"]["mail_express"], tail["mu_ln_min"], tail["sigma"], limit)
    assert band["low"] <= share <= band["high"]
    shares = [_share_within(p, tail["mu_ln_min"], tail["sigma"], limit) for p in prof["p_on_time"].values()]
    assert shares == sorted(shares, reverse=True), "premium should be most punctual, passenger least"
