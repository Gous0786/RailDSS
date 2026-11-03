"""ETL stage 3 tests: station lines, platforms, PSA fix (issue #8 acceptance criteria)."""

from __future__ import annotations

import pandas as pd
import pytest

from raildss_etl.load import run_stage1
from raildss_etl.stations import Stage3Report, build_station_lines
from raildss_schema.entities import Platform, StationLine

Built = tuple[list[StationLine], list[Platform], Stage3Report]


@pytest.fixture(scope="module")
def built() -> Built:
    tables: dict[str, pd.DataFrame] = run_stage1(write=False)[0]
    return build_station_lines(tables)


def _line(built: Built, op: str, no: int) -> StationLine:
    return next(x for x in built[0] if x.op_code == op and x.line_no == no)


def test_counts_and_report(built: Built) -> None:
    lines, platforms, rep = built
    assert (len(lines), len(platforms)) == (103, 70)
    assert rep.ok and rep.platforms_unjoined == [] and rep.lines_missing_csr == []


def test_every_non_halt_line_has_csr(built: Built) -> None:
    halts = {"BTVA", "HCM", "DGB", "RMZ"}
    for x in built[0]:
        if x.op_code in halts:
            assert x.csr_m is None
        else:
            assert x.csr_m is not None and x.csr_m > 0, f"{x.op_code}/{x.line_no}"


def test_psa_fix(built: Built) -> None:
    psa = [x for x in built[0] if x.op_code == "PSA"]
    assert len(psa) == 10 and all(x.csr_status == "estimated" for x in psa)
    assert all(x.csr_low_m is not None and x.csr_high_m is not None for x in psa)
    assert all(x.length_m is None for x in psa)  # placeholder length 1 dropped
    line7 = _line(built, "PSA", 7)
    assert (line7.name, line7.directions) == ("UPML4", "UP")
    assert any(c.startswith("PSA/7 (UPML4)") for c in built[2].direction_conflicts)  # DQ-18 finding
    assert {x.line_no: x.directions for x in psa if x.directions != "BOTH"} == {
        4: "DN",
        5: "DN",
        6: "DN",
        7: "UP",
        10: "UP",
    }


def test_short_lines_preserved(built: Built) -> None:
    assert _line(built, "CPP", 1).csr_m == 390.0
    assert _line(built, "PDU", 1).csr_m == 300.0


def test_csr_statistics_match_appendix_b(built: Built) -> None:
    rep = built[2]
    assert (rep.loop_csr_median_m, rep.loop_csr_n) == (733.5, 40)
    assert (rep.main_csr_median_m, rep.main_csr_n) == (752.5, 42)


def test_platforms_join_their_lines(built: Built) -> None:
    lines, platforms, _ = built
    keys = {(x.op_code, x.line_no) for x in lines}
    assert all((p.op_code, p.station_line_no) in keys for p in platforms)
    psa_pf = {p.platform_no: p.station_line_no for p in platforms if p.op_code == "PSA"}
    assert psa_pf == {"PF-1": 8, "PF-1A": 9, "PF-2": 7, "PF-3": 6}
