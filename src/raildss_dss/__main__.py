"""Command line for the DSS services.

python -m raildss_dss ingest --trace run.jsonl     # replay a twin trace (raildss_twin run --trace-out)
python -m raildss_dss ingest --kafka [--speed 60]  # consume obs.* / ops.* live as the ``dss`` principal

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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_dss")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_in = sub.add_parser("ingest", help="DS-1 ingestion and data quality (issue #70)")
    src = p_in.add_mutually_exclusive_group(required=True)
    src.add_argument("--trace", type=Path, help="twin trace file (JSON lines) to replay")
    src.add_argument("--kafka", action="store_true", help="consume obs.* / ops.* from Kafka")
    p_in.add_argument("--speed", type=float, default=1.0, help="feed seconds per wall second (--kafka)")
    p_in.add_argument("--max-idle-s", type=float, default=60.0, help="stop after this long without a record")
    args = ap.parse_args(argv)

    ing = Ingestor(on_alert=_print_alert)
    if args.trace:
        ing.run(JsonLinesTransport(args.trace))
    else:
        _kafka(ing, args.speed, args.max_idle_s)
    _report(ing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
