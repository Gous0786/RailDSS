"""BL-0 reference check (issue #93): the committed note matches a fresh BL-0 run of the day."""

from __future__ import annotations

import datetime as dt

from raildss_twin import bl0_check

DAY = dt.date(2025, 8, 20)


def test_note_is_up_to_date_and_states_the_decision() -> None:
    text = bl0_check.run_check(DAY)
    path = bl0_check.OUT_DIR / bl0_check.note_name(DAY)
    assert path.read_text(encoding="utf-8") == text, "run: python -m raildss_twin bl0-check --write"
    assert "no passenger actuals" in text
    assert "## Decision" in text
    assert "accepted as the do-nothing reference" in text
