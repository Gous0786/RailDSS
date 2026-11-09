"""DS-1 ingestion and data quality (issue #70, spec FR-ING-01..07).

The feeds come from the twin's observation model (#52) on a window of 20 Aug 2025. The DSS package
never imports the twin; only these tests do, to produce realistic feeds.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path
from typing import Any

import pytest

from raildss_common.envelope import EventEnvelope
from raildss_dss.ingest import BY_TOPIC, Ingestor, JsonLinesTransport, MemoryTransport
from raildss_twin import run
from raildss_twin.observe import Observer, load_profile
from raildss_twin.plans import day_start
from tests.common.events import MALFORMED, valid_fixtures

DAY = dt.date(2025, 8, 20)
HOUR = 3600.0
WINDOW_S = 6 * HOUR


def records(events: list[tuple[str, EventEnvelope]]) -> list[tuple[str, bytes]]:
    return [(t, e.to_json()) for t, e in events]


@pytest.fixture(scope="module")
def world() -> run.World:
    return run.load_world()


@pytest.fixture(scope="module")
def feeds(world: run.World) -> dict[str, Any]:
    """One window observed cleanly (D0), with 5 % duplicates and 1 % out of order, and with outages."""
    d0 = load_profile(world.register, "D0")
    noisy = dataclasses.replace(d0, duplicates=0.05, out_of_order=0.01)  # same name: same event ids
    outage = dataclasses.replace(
        load_profile(world.register, "D1"), name="OUTAGE", outage_every_s=2 * HOUR, outage_s=(900.0, 900.0)
    )
    obs = {
        "clean": Observer(world.register, d0, keep=True),
        "noisy": Observer(world.register, noisy, keep=True),
        "outage": Observer(world.register, outage, keep=True),
    }
    run.run_day(world, DAY, until_s=WINDOW_S, observers=list(obs.values()))
    return obs


def ingest(events: list[tuple[str, EventEnvelope]], **kw: Any) -> Ingestor:
    ing = Ingestor(**kw)
    ing.run(MemoryTransport(records(events)))
    return ing


# --------------------------------------------------------------------------- acceptance criteria
def test_duplicates_and_out_of_order_leave_the_ingested_stream_identical_to_d0(feeds: dict[str, Any]) -> None:
    clean, noisy = feeds["clean"], feeds["noisy"]
    assert len(noisy.events) > len(clean.events)  # duplicates were injected
    a, b = ingest(clean.events), ingest(noisy.events)
    assert a.state_hash() == b.state_hash()
    assert [e.event_id for e in a.ordered()] == [e.event_id for e in b.ordered()]
    coa = b.summary()["COA"]
    assert coa["duplicates"] > 0 and coa["out_of_order"] > 0
    assert 0.03 <= coa["duplicate_share"] <= 0.07  # 5 % injected on station events
    assert b.processor.stats.duplicates == coa["duplicates"]


def test_a_source_outage_raises_a_data_quality_alert_within_the_configured_lag(
    feeds: dict[str, Any],
) -> None:
    o = feeds["outage"]
    ing = ingest(o.events)
    limit = dt.timedelta(seconds=ing.silence_s + ing.heartbeat_s)  # + one heartbeat period of phase
    origin = day_start(DAY)
    stale = [a for a in ing.alerts if a.kind == "DATA_STALE"]
    assert {a.source for a in stale} == {"SIM.RTIS", "SIM.COA", "SIM.BLOCK", "SIM.LOGGER"}
    for start_s, end_s in o.outages:
        start, end = origin + dt.timedelta(seconds=start_s), origin + dt.timedelta(seconds=end_s)
        raised = [a for a in stale if start <= a.time <= end]
        assert raised, f"no alert in the outage {start}..{end}"
        assert all(a.time - start <= limit for a in raised), [(a.source, a.time - start) for a in raised]
        assert all(a.priority == "P4" for a in raised)
    recovered = [a for a in ing.alerts if a.kind == "SOURCE_RECOVERED"]
    assert len(recovered) == len(stale) - len(ing.stale_sources)  # an outage may outlast the run


def test_no_alert_and_no_rejection_on_a_clean_feed(feeds: dict[str, Any]) -> None:
    ing = ingest(feeds["clean"].events)
    assert ing.alerts == [] and ing.rejections == []
    assert all(not e.quality.late for e in ing.released)
    assert len(ing.released) == len(ing.history)


# --------------------------------------------------------------------------- FR-ING-01 validation and DLQ
def test_malformed_records_are_dead_lettered_and_valid_ones_are_not() -> None:
    dlq: list[Any] = []
    ing = Ingestor(dead_letter=dlq.append)
    for _, bad in MALFORMED:
        assert ing.ingest("obs.position", json.dumps(bad).encode()) == "rejected"
    assert ing.ingest("obs.station", b"{not json") == "rejected"
    assert len(dlq) == len(MALFORMED) + 1 and all(r.reason for r in dlq)
    valid = [(t, e) for _, t, e in valid_fixtures() if t in BY_TOPIC]
    assert valid
    for topic, event in valid:
        assert ing.ingest(topic, json.dumps(event).encode()) == "delivered", topic
    assert len(dlq) == len(MALFORMED) + 1


def test_a_valid_envelope_on_the_wrong_topic_is_rejected() -> None:
    dlq: list[Any] = []
    ing = Ingestor(dead_letter=dlq.append)
    _, _, position = valid_fixtures()[0]
    ing.ingest("obs.station", json.dumps(position).encode())
    assert len(dlq) == 1 and "not carried by topic obs.station" in dlq[0].reason


# --------------------------------------------------------------------------- FR-ING-03 late events
def test_an_event_six_minutes_behind_its_source_is_late_but_kept() -> None:
    _, _, base = valid_fixtures()[0]
    t0 = dt.datetime(2025, 8, 20, 6, 0, tzinfo=dt.UTC)

    def fix(seq: int, at: dt.datetime) -> bytes:
        ev = dict(base, seq=seq, event_id=f"00000000-0000-4000-8000-{seq:012d}")
        ev.update(event_time=at.isoformat(), ingest_time=(t0 + dt.timedelta(minutes=10)).isoformat())
        return json.dumps(ev).encode()

    ing = Ingestor()
    ing.ingest("obs.position", fix(2, t0 + dt.timedelta(minutes=10)))
    ing.ingest("obs.position", fix(1, t0 + dt.timedelta(minutes=4)))  # 6 min behind the newest
    late = [e for e in ing.released if e.quality.late]
    assert [e.seq for e in late] == [1]
    assert [e.seq for e in ing.ordered()] == [1, 2]
    assert ing.summary()["RTIS"]["late"] == 1


# --------------------------------------------------------------------------- FR-ING-05 stale trains
def test_stale_boundary_is_120_seconds() -> None:
    _, _, base = valid_fixtures()[0]
    ing = Ingestor()
    ing.ingest("obs.position", json.dumps(base).encode())
    last = dt.datetime.fromisoformat(base["event_time"])
    at = {s: ing.train_fixes(last + dt.timedelta(seconds=s))[0] for s in (119, 120, 121)}
    assert [at[s].stale for s in (119, 120, 121)] == [False, False, True]
    assert at[121].age_s == 121


# --------------------------------------------------------------------------- FR-ING-06 metrics
def test_metrics_per_source(feeds: dict[str, Any]) -> None:
    summary = ingest(feeds["clean"].events).summary()
    assert {"RTIS", "COA", "BLOCK_INSTRUMENTS", "LOGGER_EI", "FEED_HEALTH"} <= set(summary)
    assert summary["RTIS"]["completeness"] == pytest.approx(1.0, abs=0.01)
    assert summary["COA"]["latency_p95_s"] < 1
    assert all(s["rejected"] == 0 for s in summary.values())


# --------------------------------------------------------------------------- FR-ING-07 adapter contract
@pytest.mark.parametrize("transport", ["memory", "jsonl"])
def test_every_transport_gives_the_same_ingested_state(
    feeds: dict[str, Any], transport: str, tmp_path: Path
) -> None:
    events = feeds["noisy"].events
    if transport == "memory":
        source: MemoryTransport | JsonLinesTransport = MemoryTransport(records(events))
    else:
        path = tmp_path / "trace.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"topic": "gt.state", "event": {"ground": "truth"}}) + "\n")  # never read
            for t, e in events:
                fh.write(json.dumps({"topic": t, "event": json.loads(e.to_json())}) + "\n")
        source = JsonLinesTransport(path)
    ing = Ingestor()
    ing.run(source)
    assert ing.rejections == []
    assert ing.state_hash() == ingest(feeds["clean"].events).state_hash()


def test_every_adapter_names_an_ir_system_and_a_catalogue_topic() -> None:
    from raildss_common.topics import load_catalogue

    catalogue = load_catalogue()
    for topic, adapter in BY_TOPIC.items():
        assert catalogue.can_read("dss", topic)
        assert not catalogue.topic(topic).is_ground_truth
        assert adapter.system.isupper() and adapter.event_types
