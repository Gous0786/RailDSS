"""Licence review guard (issue #23, DR-0001; spec FR-DATA-05, NFR-LIC-03/04, OD-16, OD-17).

* Every provenance card that cites OpenStreetMap, the Copernicus DEM or SRTM carries the
  attribution text those licences require, verbatim.
* The root NOTICE carries the same third-party notices (Apache-2.0 §4(d) passes it on).
* The decision record names the train-graph component and its fallback.
* The DISPLIB licence request has a recorded status.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DECISION = ROOT / "docs" / "decisions" / "0001-licence-review-m0.md"
DISPLIB_REQUEST = ROOT / "docs" / "licensing" / "displib-licence-request.md"
NOTICE = ROOT / "NOTICE"

OSM_ATTRIBUTION = ("© OpenStreetMap contributors", "https://www.openstreetmap.org/copyright", "ODbL")
COPERNICUS_MODIFIED = (
    "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH "
    "2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"
)
COPERNICUS_DISCLAIMER = (
    "The organisations in charge of the Copernicus programme by law or by delegation do not incur "
    "any liability for any use of the Copernicus WorldDEM-30."
)
SRTM_CITATION = "https://doi.org/10.5067/MEaSUREs/SRTM/SRTMGL1.003"
DISPLIB_STATUSES = ("drafted, awaiting user to send", "sent, awaiting reply", "answered")


def _cards() -> list[Path]:
    return sorted((ROOT / "data").rglob("PROVENANCE*.md"))


def required_attribution(text: str) -> list[str]:
    """Return the attribution strings that ``text`` must contain, given the sources it cites."""
    required: list[str] = []
    if re.search(r"OpenStreetMap|\bOSM\b", text):
        required += OSM_ATTRIBUTION
    if "Copernicus" in text:
        required += [COPERNICUS_MODIFIED, COPERNICUS_DISCLAIMER]
    if re.search(r"\bSRTM\b", text):
        required.append(SRTM_CITATION)
    return required


def missing_attribution(text: str) -> list[str]:
    return [s for s in required_attribution(text) if s not in text]


def test_cards_exist() -> None:
    names = {p.relative_to(ROOT).as_posix() for p in _cards()}
    assert "data/derived/ktv-psa/PROVENANCE.md" in names
    assert "data/external/osm-dem/PROVENANCE.md" in names


@pytest.mark.parametrize("card", _cards(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_provenance_cards_carry_attribution(card: Path) -> None:
    assert missing_attribution(card.read_text(encoding="utf-8")) == []


def test_guard_detects_missing_attribution() -> None:
    assert missing_attribution("Raw inputs: OpenStreetMap (ODbL)") != []
    assert COPERNICUS_DISCLAIMER in missing_attribution(f"Gradients from Copernicus. {COPERNICUS_MODIFIED}")
    assert missing_attribution("No third-party sources here.") == []


def test_notice_carries_third_party_notices() -> None:
    text = NOTICE.read_text(encoding="utf-8")
    for s in (*OSM_ATTRIBUTION, COPERNICUS_MODIFIED, COPERNICUS_DISCLAIMER):
        assert s in text


def test_decision_record_names_chart_choice() -> None:
    text = DECISION.read_text(encoding="utf-8")
    assert re.search(r"Chart component: OSRD `@osrd-project/ui-charts` SpaceTimeChart", text)
    assert "D3 + Canvas" in text  # fallback named (REC-19)
    for ref in ("OD-16", "OD-17", "NFR-LIC-03", "NFR-LIC-04"):
        assert ref in text
    assert re.search(r"\| Status \| \*\*(Proposed|Accepted)", text)


def test_displib_request_status_recorded() -> None:
    text = DISPLIB_REQUEST.read_text(encoding="utf-8")
    rows = re.findall(r"^\| \d{4}-\d{2}-\d{2} \| \*\*(.+?)\*\* \|", text, flags=re.M)
    assert rows, "status table has no dated rows"
    assert rows[-1] in DISPLIB_STATUSES
