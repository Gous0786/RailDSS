"""Command line for the DSS services.

python -m raildss_dss ingest --trace run.jsonl     # replay a twin trace (raildss_twin run --trace-out)
python -m raildss_dss ingest --kafka [--speed 60]  # consume obs.* / ops.* live as the ``dss`` principal
python -m raildss_dss state --trace run.jsonl --every-min 10   # DS-3 snapshots S_t of a replayed trace
python -m raildss_dss predict-eval [--write]                   # DS-4 ETA accuracy on held-out log days

Both print the per-source data-quality metrics, the data-quality alerts and the stale trains at the end
(``--kafka`` stops on Ctrl+C or after ``--max-idle-s`` without a record). Rejected records go to the
topic's dead-letter topic in ``--kafka`` mode and are listed in ``--trace`` mode.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from raildss_dss.ingest import ADAPTERS, DqAlert, Ingestor, JsonLinesTransport
from raildss_dss.state import PlanDirectory, StateEstimator

NEWLINE = "\n"


def _print_alert(alert: DqAlert) -> None:
    print(f"ALERT {alert.priority} {alert.kind} {alert.source} at {alert.time.isoformat()}: {alert.detail}")


def _report(ing: Ingestor) -> None:
    print(json.dumps(ing.summary(), indent=1, default=str))
    stale = [f for f in ing.train_fixes() if f.stale]
    print(f"released {len(ing.released)} events, history hash {ing.state_hash()}")
    print(f"trains with a fix: {len(ing.train_fixes())}, stale now: {len(stale)}")
    for r in ing.rejections[:20]:
        print(f"REJECTED {r.topic}: {r.reason}")


def _kafka(ing: Ingestor, speed: float, max_idle_s: float) -> None:
    from raildss_common.bus import EventConsumer, EventProducer, KafkaSettings
    from raildss_common.topics import load_catalogue

    settings, catalogue = KafkaSettings.from_env(), load_catalogue()
    consumer = EventConsumer(
        settings,
        catalogue,
        group_id=f"{settings.username or 'dss'}.ingest",
        topics=[a.topic for a in ADAPTERS],
        processor=ing.processor,
        dlq_producer=EventProducer(settings, catalogue),
    )
    last_wall = time.monotonic()
    try:
        while time.monotonic() - last_wall < max_idle_s:
            if consumer.poll(1.0) is not None:
                last_wall = time.monotonic()
            elif ing.now is not None:  # the feed clock runs on while nothing arrives
                ing.tick(ing.now + dt.timedelta(seconds=speed))
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()
        ing.flush()


def _state(trace: Path, every_min: float, out: Path | None) -> None:
    ing = Ingestor()
    ing.run(JsonLinesTransport(trace))
    events = sorted(ing.released, key=lambda e: e.event_time)
    if not events:
        print("no events")
        return
    est = StateEstimator(PlanDirectory.from_published())
    step = dt.timedelta(minutes=every_min)
    t, i, last = events[0].event_time, 0, None
    while t <= events[-1].event_time + step:
        while i < len(events) and events[i].event_time <= t:
            est.apply(events[i])
            i += 1
        last = est.snapshot(t)
        stale = sum(s.stale for s in last.trains)
        print(
            f"{t.isoformat()}  v{last.version:<5} trains {len(last.trains):>3} stale {stale:>3}  "
            f"{last.hash[:16]}"
        )
        t += step
    if out is not None and last is not None:
        out.write_text(json.dumps(last.to_json(), indent=1) + NEWLINE, encoding="utf-8", newline=NEWLINE)
        print(f"wrote the last snapshot to {out}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_dss")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest", help="DS-1 ingestion and data quality (issue #70)")
    src = p_in.add_mutually_exclusive_group(required=True)
    src.add_argument("--trace", type=Path, help="twin trace file (JSON lines) to replay")
    src.add_argument("--kafka", action="store_true", help="consume obs.* / ops.* from Kafka")
    p_in.add_argument("--speed", type=float, default=1.0, help="feed seconds per wall second (--kafka)")
    p_in.add_argument("--max-idle-s", type=float, default=60.0, help="stop after this long without a record")
    p_st = sub.add_parser("state", help="DS-3 state snapshots of a replayed twin trace (issue #71)")
    p_st.add_argument("--trace", type=Path, required=True, help="twin trace file (JSON lines)")
    p_st.add_argument("--every-min", type=float, default=10.0, help="snapshot interval in minutes")
    p_st.add_argument("--out", type=Path, default=None, help="write the last snapshot as JSON")
    p_ev = sub.add_parser("predict-eval", help="ETA accuracy on the held-out freight-log days (issue #72)")
    p_ev.add_argument("--write", action="store_true", help="write docs/dss/eta-evaluation.md")
    args = ap.parse_args(argv)

    if args.cmd == "predict-eval":
        from raildss_dss.predict import EVAL_DOC, run_evaluation

        doc = run_evaluation().render_markdown()
        if args.write:
            EVAL_DOC.parent.mkdir(parents=True, exist_ok=True)
            EVAL_DOC.write_text(doc, encoding="utf-8", newline=NEWLINE)
            print(f"wrote {EVAL_DOC}")
        else:
            sys.stdout.write(doc)
        return 0
    if args.cmd == "state":
        _state(args.trace, args.every_min, args.out)
        return 0

    ing = Ingestor(on_alert=_print_alert)
    if args.trace:
        ing.run(JsonLinesTransport(args.trace))
    else:
        _kafka(ing, args.speed, args.max_idle_s)
    _report(ing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
