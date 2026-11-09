"""Command line for the twin.

python -m raildss_twin calibrate [--write]    # running-time calibration (issue #16)
python -m raildss_twin run --date 2025-08-20  # one real day under BL-0 (issue #20)
python -m raildss_twin displib --from-h 0 --to-h 2  # DISPLIB-style IR of a window (issue #22)

``run`` is headless by default. ``--speed N`` (1 to 60) follows the wall clock at N times real time;
``--control`` reads ``pause``, ``resume`` and ``speed N`` from standard input while it runs.
``--kafka`` streams every ``gt.*`` event to Kafka as the ``twin`` principal (RAILDSS_KAFKA_* settings).
``--profile`` (default D1, OD-24) also publishes the observed ``obs.*`` feeds degraded per that profile
(issue #52); ``--profile none`` publishes ground truth only.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import threading
from pathlib import Path

from raildss_common.envelope import EventEnvelope
from raildss_twin import calibration
from raildss_twin.injector import SECONDS_PER_MIN
from raildss_twin.sim import SECONDS_PER_HOUR, Sink


def _incident(word: list[str], now_s: float) -> object:
    """``inject <kind> <where> <minutes> [<announced ETR minutes>]`` -> a Disruption starting now.

    ``where`` is a block id (line_block, signal_failure, tsr; ``block/line`` for one line), a
    ``station/line`` (point_failure) or a train run id (loco_failure). A TSR takes ``@<km/h>``.
    """
    from raildss_twin.injector import Disruption

    kind, where, minutes = word[1], word[2], float(word[3])
    fields: dict[str, object] = {
        "incident_id": f"INC-{int(now_s)}",
        "kind": kind,
        "start_s": now_s,
        "duration_s": minutes * SECONDS_PER_MIN,
    }
    if word[4:] and not word[4].startswith("@"):
        fields["etr_s"] = now_s + float(word[4]) * SECONDS_PER_MIN
    speed = next((w[1:] for w in word[4:] if w.startswith("@")), None)
    if kind == "point_failure":
        station, _, line_no = where.partition("/")
        fields |= {"station": station, "line_no": int(line_no)}
    elif kind == "loco_failure":
        fields["run_id"] = where
    else:
        block, _, line_id = where.partition("/")
        fields |= {
            "block_id": block,
            "line_id": line_id or None,
            "speed_kmh": float(speed) if speed else None,
        }
    return Disruption.model_validate(fields)


def _order(word: list[str], now_s: float) -> object:
    """``order <id> <station> <run id> line <n>`` or ``... hold <minutes>``."""
    from raildss_twin.agents import Order

    oid, station, run_id, what, value = word[1:6]
    if what == "line":
        return Order(
            order_id=oid, station=station, run_id=run_id, action="receive_on_line", line_no=int(value)
        )
    return Order(
        order_id=oid,
        station=station,
        run_id=run_id,
        action="hold_until",
        until_s=now_s + float(value) * SECONDS_PER_MIN,
    )


def _control(clock: object, extras: dict[str, object]) -> None:
    """Twin console: pause | resume | speed N | inject ... | order ... (FR-SCN-03, FR-TWIN-15)."""
    from raildss_twin.agents import Order, StationMasters
    from raildss_twin.clock import Clock
    from raildss_twin.injector import Disruption, Injector

    assert isinstance(clock, Clock)
    for line in sys.stdin:
        word = line.split()
        try:
            if word[:1] == ["pause"]:
                clock.pause()
            elif word[:1] == ["resume"]:
                clock.resume()
            elif word[:1] == ["speed"] and word[1:]:
                clock.set_speed(float(word[1]))
            elif word[:1] == ["inject"] and isinstance(extras.get("injector"), Injector):
                inj = extras["injector"]
                assert isinstance(inj, Injector)
                d = _incident(word, inj.sim.env.now)
                assert isinstance(d, Disruption)
                inj.inject(d)
                print(f"control: injected {d.incident_id} ({d.kind})", file=sys.stderr)
            elif word[:1] == ["order"] and isinstance(extras.get("agents"), StationMasters):
                sm = extras["agents"]
                assert isinstance(sm, StationMasters)
                o = _order(word, sm.sim.env.now)
                assert isinstance(o, Order)
                sm.receive(o)
        except (ValueError, IndexError) as exc:
            print(f"control: {exc}", file=sys.stderr)


def _run(args: argparse.Namespace) -> int:
    from raildss_twin import run
    from raildss_twin.clock import Clock
    from raildss_twin.observe import Observer

    world = run.load_world()
    cfg = world.register.value("twin.sim")
    clock = Clock(args.speed, float(cfg["max_speed_factor"])) if args.speed else None
    extras: dict[str, object] = {}
    if clock is not None and args.control:
        threading.Thread(target=_control, args=(clock, extras), daemon=True).start()
    sinks: list[Sink] = []
    producer = None
    if args.kafka:
        from raildss_common.bus import EventProducer, KafkaSettings
        from raildss_common.topics import load_catalogue

        producer = EventProducer(KafkaSettings.from_env(), load_catalogue())
        sinks.append(producer.publish)
    trace = args.trace_out.open("w", encoding="utf-8", newline="\n") if args.trace_out else None
    if trace is not None:
        out = trace

        def write_trace(topic: str, event: EventEnvelope) -> None:
            record = {"topic": topic, "event": json.loads(event.to_json())}
            out.write(json.dumps(record, sort_keys=True) + "\n")

        sinks.append(write_trace)

    def sink(topic: str, event: EventEnvelope) -> None:
        for s in sinks:
            s(topic, event)

    day = dt.date.fromisoformat(args.date)
    until = args.until_h * SECONDS_PER_HOUR if args.until_h else None
    observers = [] if args.profile == "none" else [Observer(world.register, args.profile)]
    from raildss_twin.agents import StationMasters
    from raildss_twin.injector import Disruption, Injector

    scripted = (
        [Disruption.model_validate(d) for d in json.loads(args.inject.read_text(encoding="utf-8"))]
        if args.inject
        else []
    )

    def setup(sim: object) -> None:
        from raildss_twin.sim import Sim

        assert isinstance(sim, Sim)
        extras["injector"] = Injector(sim, scripted)
        extras["agents"] = StationMasters(sim)

    report, _ = run.run_day(
        world,
        day,
        seed=args.seed,
        sink=sink if sinks else None,
        clock=clock,
        until_s=until,
        observers=observers,
        setup=setup,
        perturb=args.perturb,
    )
    if producer is not None:
        producer.flush()
    if trace is not None:
        trace.close()
    print(json.dumps(run.summary(report), indent=1))
    for o in observers:
        print(f"observed ({o.profile.name}): {json.dumps(dict(sorted(o.counts.items())))}")
    for d in report.deadlocks:
        print(f"DEADLOCK at {d.time_s:.0f} s: " + "; ".join(f"{t.run_id} at {t.location}" for t in d.trains))
    for s in report.stuck:
        print(f"STUCK {s.run_id} ({s.kind}) at {s.location}: {s.reason}")
    for u in report.unsupported:
        print(f"UNSUPPORTED {u['run_id']}: {u['reason']}")
    if args.report_out:
        args.report_out.write_text(report.model_dump_json(indent=1) + "\n", encoding="utf-8", newline="\n")
    print(
        f"run: {'OK' if report.completed else 'INCOMPLETE'} in {report.wall_s} s, trace {report.trace_hash}"
    )
    return 0 if report.completed or until else 1


def _displib(args: argparse.Namespace) -> int:
    from raildss_etl.mapping import REPO
    from raildss_twin import displib, run
    from raildss_twin.sim import SECONDS_PER_HOUR

    world = run.load_world()
    day = dt.date.fromisoformat(args.date)
    start, end = args.from_h * SECONDS_PER_HOUR, args.to_h * SECONDS_PER_HOUR if args.to_h else None
    instance, plans = displib.build_instance(world, day, start, end)
    counts = instance.counts
    print(f"window {args.date} {args.from_h:g} h to {args.to_h:g} h: {len(plans)} trains")
    for name, n in counts.items():
        print(f"  {name:<22}{n:>8}")
    out = args.out or REPO / "data" / "interim" / "ktv-psa" / "displib"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{instance.scenario_id}-{args.from_h:g}-{args.to_h:g}h"
    plain = json.dumps(instance.to_displib(), separators=(",", ":"))
    (out / f"{stem}.ir.json").write_text(instance.to_json(), encoding="utf-8", newline="\n")
    (out / f"{stem}.displib.json").write_text(plain + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {stem}.ir.json and {stem}.displib.json to {out}")
    return 0


def _chart(args: argparse.Namespace) -> int:
    from raildss_etl.mapping import REPO
    from raildss_twin import chart, run

    world = run.load_world()
    day = dt.date.fromisoformat(args.date)
    data = chart.chart_data(world, day)
    out = args.out or REPO / "web" / "public" / "data"
    out.mkdir(parents=True, exist_ok=True)
    nl = "\n"
    (out / f"{day.isoformat()}.json").write_text(
        json.dumps(data, separators=(",", ":")) + nl, encoding="utf-8", newline=nl
    )
    index = out / "index.json"
    days = set(json.loads(index.read_text(encoding="utf-8"))["days"]) if index.exists() else set()
    index.write_text(
        json.dumps({"days": sorted(days | {day.isoformat()})}) + nl, encoding="utf-8", newline=nl
    )
    print(f"chart {day}: {len(data['trains'])} trains, {len(data['stations'])} stations -> {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_twin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_cal = sub.add_parser("calibrate", help="calibrate running times against the freight log and the WTT")
    p_cal.add_argument("--write", action="store_true", help="write the calibration file and the report")
    p_run = sub.add_parser("run", help="simulate one real day under BL-0")
    p_run.add_argument("--date", default="2025-08-20", help="WTT date and freight day (default 2025-08-20)")
    p_run.add_argument("--seed", type=int, default=None)
    p_run.add_argument(
        "--perturb", action="store_true", help="seeded entry delays and run-time / dwell noise (FR-TWIN-11)"
    )
    p_run.add_argument("--speed", type=float, default=None, help="clock multiple 1..60; default headless")
    p_run.add_argument("--until-h", type=float, default=None, help="stop after this many simulated hours")
    p_run.add_argument("--kafka", action="store_true", help="stream gt.* and obs.* events to Kafka")
    p_run.add_argument(
        "--profile",
        choices=["D0", "D1", "D2", "D3", "none"],
        default="D1",
        help="degradation profile of the obs.* feeds (default D1); none publishes ground truth only",
    )
    p_run.add_argument("--control", action="store_true", help="read pause/resume/speed N from stdin")
    p_run.add_argument(
        "--trace-out", type=Path, default=None, help="write every gt.* and obs.* event as JSON lines"
    )
    p_run.add_argument("--report-out", type=Path, default=None)
    p_run.add_argument(
        "--inject", type=Path, default=None, help="JSON list of scripted disruptions (raildss_twin.injector)"
    )
    p_dl = sub.add_parser("displib", help="convert a window of a day to the DISPLIB-style IR (issue #22)")
    p_dl.add_argument("--date", default="2025-08-20")
    p_dl.add_argument("--from-h", type=float, default=0.0)
    p_dl.add_argument("--to-h", type=float, default=2.0, help="0 converts to the end of the day")
    p_dl.add_argument(
        "--out", type=Path, default=None, help="directory (default data/interim/ktv-psa/displib)"
    )
    p_ch = sub.add_parser("chart", help="export the control-chart data of a day for the web UI (issue #86)")
    p_ch.add_argument("--date", default="2025-08-20")
    p_ch.add_argument("--out", type=Path, default=None, help="directory (default web/public/data)")
    args = ap.parse_args(argv)

    if args.cmd == "run":
        return _run(args)
    if args.cmd == "displib":
        return _displib(args)
    if args.cmd == "chart":
        return _chart(args)
    cal, report = calibration.run(record_reference=args.write)
    doc = calibration.render_markdown(report)
    if args.write:
        calibration.write(cal, report)
        calibration.REPORT_DOC.parent.mkdir(parents=True, exist_ok=True)
        calibration.REPORT_DOC.write_text(doc, encoding="utf-8", newline="\n")
        print(f"wrote {calibration.CALIBRATION_FILE.name} and {calibration.REPORT_DOC.name}")
    else:
        sys.stdout.write(doc)
    status = "OK" if report.ok else "FAILED"
    print(f"calibration: {status} (passenger target met: {report.passenger_target_met})")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
