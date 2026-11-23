"""BL-0 reference check (issue #93): the committed note matches a fresh BL-0 run of the day."""

from __future__ import annotations

import datetime as dt
from typing import Any

from raildss_twin import bl0_check

DAY = dt.date(2025, 8, 20)


def test_note_is_up_to_date_and_states_the_decision() -> None:
    text = bl0_check.run_check(DAY)
    path = bl0_check.OUT_DIR / bl0_check.note_name(DAY)
    assert path.read_text(encoding="utf-8") == text, "run: python -m raildss_twin bl0-check --write"
    assert "no passenger actuals" in text
    assert "## Decision" in text
    assert "accepted as the do-nothing reference" in text


def _summary(**over: Any) -> dict[str, Any]:
    """A hand-made summary of a day, as ``bl0_check.summarise`` returns it."""
    s: dict[str, Any] = {
        "day": "2025-01-01",
        "kpis": {
            "A4_max_arrival_delay_min": 150.0,
            "A2_section_punctuality_pct": 60.0,
            "C2_freight_detention_h": 5.0,
        },
        "pax_n": 100,
        "pax_mean": 20.0,
        "pax_median": 2.0,
        "pax_max": 140.0,
        "pax_over_long": 10,
        "frt_n": 50,
        "frt_stabled": 2,
        "frt_hist_median": 6.0,
        "frt_hist_p90": 11.0,
        "frt_hist_mean": 6.5,
        "frt_bl0_median": 1.0,
        "frt_bl0_p90": 3.0,
        "frt_bl0_mean": 2.0,
        "hist_detention_h": 100.0,
        "hist_running_excess_h": 80.0,
    }
    return {**s, **over}


def test_the_delay_sentence_follows_the_table_for_any_day() -> None:
    tail = bl0_check.delay_shape(_summary())
    assert "median exit delay is 2.0 min against a mean of 20.0 min" in tail
    assert "tail of long delays" in tail and "10 trains are more than 60 min late" in tail
    assert "2.5 h" in tail  # the longest arrival delay, 150 min, from the KPI
    spread = bl0_check.delay_shape(_summary(pax_median=15.0, pax_mean=20.0))
    assert "median exit delay is 15.0 min" in spread and "spread over many trains" in spread
    assert "tail of long delays" not in spread
    none = bl0_check.delay_shape(_summary(pax_over_long=0, pax_median=0.4, pax_mean=1.0))
    assert "No train is more than 60 min late" in none and "tail" not in none


def test_the_freight_sentence_says_faster_slower_or_about_the_same() -> None:
    head, text = bl0_check.freight_comparison(_summary())
    assert head == "Freight is faster in BL-0 than in the log." and "6.0 times slower than BL-0" in text
    head, text = bl0_check.freight_comparison(_summary(frt_hist_median=1.0, frt_bl0_median=3.0))
    assert head == "Freight is slower in BL-0 than in the log." and "BL-0 is 3.0 times slower" in text
    head, text = bl0_check.freight_comparison(_summary(frt_hist_median=1.0, frt_bl0_median=1.1))
    assert head == "Freight takes about as long in BL-0 as in the log." and "1.00 h (history)" in text


def test_the_rendered_note_has_no_figure_that_the_table_does_not_show() -> None:
    """Issue #149: nothing left over from one day (no 'under a minute', no '2.9 h'); sentences carry the table."""
    low = bl0_check.render(_summary(pax_median=0.5, pax_mean=18.0))
    high = bl0_check.render(_summary(pax_median=4.2, pax_mean=24.4))
    for text, median in ((low, "0.5"), (high, "4.2")):
        assert "under a minute" not in text and "2.9 h" not in text
        assert f"| Median exit delay (min) | {median} |" in text  # the table
        assert f"The median exit delay is {median} min" in text  # and the sentence says the same
        assert f"Median {median} min, mean" in text
