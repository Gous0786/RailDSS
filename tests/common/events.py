"""Event fixtures: every example in spec §6.2 and §7.1 as a full envelope, plus malformed ones.

Spec placeholders are replaced with concrete values: ``"uuid"`` -> a real UUID, ``"…"`` ->
a plausible id or time, ``{…}`` -> a small object, ``<param>`` -> a number, ``MaintBlock`` ->
a sketch object (its payload model belongs to a later issue).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

SPEC_6_2_EXAMPLE: dict[str, Any] = {
    "event_id": "6f1c2b8e-3d4a-4c5b-9e7f-1a2b3c4d5e6f",
    "event_type": "TRAIN_POSITION",
    "schema_version": "1.0",
    "source": "SIM.RTIS",
    "section_id": "KTV-PSA",
    "network_version": "KTV-PSA-2025-08",
    "scenario_id": "aug25-day-14-slw-cpp-sgdm",
    "seq": 18234,
    "event_time": "2026-11-02T04:12:30Z",
    "ingest_time": "2026-11-02T04:12:41Z",
    "train_run_id": "12704@2026-11-02",
    "loco_id": "WAP7-30xxx",
    "location": {
        "ne_id": "KTV-PSA-DN-0412",
        "offset_m": 312.5,
        "km": 61.20,
        "lat": 18.33,
        "lon": 83.55,
        "accuracy_m": 15,
    },
    "payload": {"speed_kmh": 74.0, "heading_deg": 168},
    "quality": {"simulated_noise": True, "late": False, "duplicate_of": None},
}

_T = "2026-11-02T04:12:30Z"
_LOC = {"km": 61.2}

# (name, topic, event_type, source, payload, envelope overrides)
_SPEC_7_1: list[tuple[str, str, str, str, dict[str, Any], dict[str, Any]]] = [
    (
        "rtis-position",
        "obs.position",
        "TRAIN_POSITION",
        "SIM.RTIS",
        {"speed_kmh": 74.0, "heading_deg": 168},
        {"location": _LOC},
    ),
    (
        "coa-station-event",
        "obs.station",
        "STATION_EVENT",
        "SIM.COA",
        {
            "station": "CPP",
            "kind": "ARR",
            "line_used": "5",
            "manual": True,
            "reported_time": "2026-11-02T04:12:00Z",
        },
        {},
    ),
    (
        "logger-occupancy",
        "obs.occupancy",
        "TDS_OCCUPANCY",
        "SIM.LOGGER",
        {"tds_id": "VZM-TC-07 (synthetic)", "occupied": True},
        {"train_run_id": None},
    ),
    (
        "block-line-clear",
        "obs.block",
        "LINE_CLEAR",
        "SIM.BLOCK",
        {
            "block_section": "CPP-SGDM",
            "line": "UP",
            "kind": "LINE_CLEAR_GRANTED",
            "train_run_id": "12704@2026-11-02",
        },
        {},
    ),
    (
        "caution-order",
        "ops.tsr",
        "TSR_ISSUED",
        "SIM.CAUTION",
        {
            "tsr_id": "TSR-0007",
            "km_from": 61.2,
            "km_to": 62.0,
            "vmax_kmh": 30,
            "reason": "engineering",
            "valid_from": "2026-11-02T00:00:00Z",
            "valid_to": "2026-11-09T00:00:00Z",
            "notice_station": "GVI",
            "caution_order_no": "CO-WAT-0412",
        },
        {"train_run_id": None},
    ),
    (
        "maintenance-block",
        "ops.blocks",
        "MAINT_BLOCK",
        "SIM.BLOCKPROG",
        {
            "block_id": "MB-0003",
            "status": "GRANTED",
            "block_section": "CPP-SGDM",
            "line": "UP",
            "from": "2026-11-02T06:00:00Z",
            "to": "2026-11-02T08:00:00Z",
        },
        {"train_run_id": None},
    ),
    (
        "disruption",
        "ops.disruptions",
        "DISRUPTION_RAISED",
        "SIM.CONTROL",
        {
            "incident_id": "INC-0031",
            "status": "RAISED",
            "type": "LOCO_FAILURE",
            "cause_code": "ELEC-LOCO",
            "location": {"km": 61.2, "ne_id": "KTV-PSA-DN-0412", "offset_m": 312.5},
            "etr": "2026-11-02T05:30:00Z",
            "etr_source": "TRD",
            "etr_sigma_min": 15,
            "lines_affected": ["DN"],
        },
        {},
    ),
    (
        "rake-ready",
        "ops.rakes",
        "RAKE_READY",
        "SIM.FOIS",
        {
            "rake_id": "RK-1104",
            "load_id": "L-88231",
            "type": "BOXN",
            "loaded": True,
            "commodity": "IORE",
            "entry_point": "KTV",
            "ready_time": "2026-11-02T03:40:00Z",
            "consist": {"wagons": 58},
        },
        {"train_run_id": None},
    ),
    (
        "crew-status",
        "ops.crew",
        "CREW_STATUS",
        "SIM.CMS",
        {
            "crew_duty_id": "CD-2201",
            "train_run_id": "FRT-88231",
            "sign_on": "2026-11-02T02:00:00Z",
            "duty_limit_min": 660,
        },
        {"train_run_id": "FRT-88231"},
    ),
    (
        "weather",
        "ops.weather",
        "WEATHER",
        "SIM.IMD",
        {"rain_mm_h": 42, "visibility_m": 800},
        {"train_run_id": None},
    ),
    (
        "heartbeat",
        "obs.heartbeat",
        "HEARTBEAT",
        "SIM.RTIS",
        {"source": "SIM.RTIS", "lag_s": 3, "count_10s": 14},
        {"train_run_id": None},
    ),
    (
        "ground-truth-state",
        "gt.state",
        "TRAIN_STATE",
        "SIM.GT",
        {"speed_kmh": 73.8, "resource": "KTV-PSA-DN-0412"},
        {"location": _LOC},
    ),
    ("ground-truth-delay", "gt.delay", "DELAY_RECORD", "SIM.GT", {"delay_s": 240, "cause": "TSR"}, {}),
]


def envelope(
    event_type: str, source: str, payload: dict[str, Any], seq: int = 1, **overrides: Any
) -> dict[str, Any]:
    base = copy.deepcopy(SPEC_6_2_EXAMPLE)
    base.update(event_type=event_type, source=source, payload=payload, seq=seq, location=None)
    base.update(overrides)
    return base


def valid_fixtures() -> list[tuple[str, str, dict[str, Any]]]:
    """(name, topic, event) for the §6.2 example and every §7.1 payload sketch."""
    out = [("spec-6.2-example", "obs.position", copy.deepcopy(SPEC_6_2_EXAMPLE))]
    for i, (name, topic, etype, source, payload, extra) in enumerate(_SPEC_7_1, 1):
        ev = envelope(etype, source, copy.deepcopy(payload), seq=i, **copy.deepcopy(extra))
        ev["event_id"] = f"00000000-0000-4000-8000-{i:012d}"
        out.append((name, topic, ev))
    return out


def _mut(fn: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    ev = copy.deepcopy(SPEC_6_2_EXAMPLE)
    fn(ev)
    return ev


MALFORMED: list[tuple[str, dict[str, Any]]] = [
    ("missing-event-id", _mut(lambda e: e.pop("event_id"))),
    ("event-id-placeholder", _mut(lambda e: e.update(event_id="uuid"))),
    ("event-id-unhyphenated", _mut(lambda e: e.update(event_id="6f1c2b8e3d4a4c5b9e7f1a2b3c4d5e6f"))),
    ("unknown-event-type", _mut(lambda e: e.update(event_type="TRAIN_TELEPORT"))),
    ("wrong-schema-version", _mut(lambda e: e.update(schema_version="2.0"))),
    ("missing-section-id", _mut(lambda e: e.pop("section_id"))),
    ("lowercase-section-id", _mut(lambda e: e.update(section_id="ktv-psa"))),
    ("negative-seq", _mut(lambda e: e.update(seq=-1))),
    ("seq-as-string", _mut(lambda e: e.update(seq="18234"))),
    ("naive-event-time", _mut(lambda e: e.update(event_time="2026-11-02T04:12:30"))),
    ("epoch-event-time", _mut(lambda e: e.update(event_time=1793592750))),
    ("unknown-top-level-field", _mut(lambda e: e.update(speed=74))),
    ("payload-not-object", _mut(lambda e: e.update(payload=[74.0]))),
    ("position-without-speed", _mut(lambda e: e.update(payload={"heading_deg": 168}))),
    ("speed-as-string", _mut(lambda e: e.update(payload={"speed_kmh": "74"}))),
    ("negative-speed", _mut(lambda e: e.update(payload={"speed_kmh": -5}))),
    ("heading-out-of-range", _mut(lambda e: e.update(payload={"speed_kmh": 74, "heading_deg": 360}))),
    ("position-without-train", _mut(lambda e: e.update(train_run_id=None))),
    ("position-without-location", _mut(lambda e: e.pop("location"))),
    ("location-lat-without-lon", _mut(lambda e: e["location"].pop("lon"))),
    ("location-latitude-out-of-range", _mut(lambda e: e["location"].update(lat=95.0))),
    ("location-without-km-or-element", _mut(lambda e: e.update(location={"lat": 18.33, "lon": 83.55}))),
    ("quality-duplicate-of-not-uuid", _mut(lambda e: e["quality"].update(duplicate_of="first"))),
    ("quality-flag-as-string", _mut(lambda e: e["quality"].update(late="no"))),
    (
        "station-event-bad-kind",
        envelope("STATION_EVENT", "SIM.COA", {"station": "CPP", "kind": "ARRIVED", "manual": True}),
    ),
    (
        "occupancy-flag-as-string",
        envelope("TDS_OCCUPANCY", "SIM.LOGGER", {"tds_id": "VZM-TC-07", "occupied": "yes"}),
    ),
    (
        "disruption-status-mismatch",
        envelope(
            "DISRUPTION_CLEARED", "SIM.CONTROL", {"incident_id": "INC-1", "status": "RAISED", "type": "X"}
        ),
    ),
    (
        "heartbeat-unknown-field",
        envelope("HEARTBEAT", "SIM.RTIS", {"source": "SIM.RTIS", "lag_s": 3, "count_10s": 14, "ok": True}),
    ),
]
