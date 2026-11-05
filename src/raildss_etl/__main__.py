"""Command line for the KTV-PSA ETL.

python -m raildss_etl mapping-check                 # mapping vs dataset headers and schema
python -m raildss_etl mapping-doc [--write]         # docs/etl/mapping-ktv-psa.md
python -m raildss_etl stage1 [--out DIR]            # load & clean -> data/interim/ktv-psa
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from raildss_etl import freight, geo, gradients, stations, timetable, topology
from raildss_etl.load import DEFAULT_OUT, run_stage1
from raildss_etl.mapping import MAPPING_DOC, REPO, check, ignored_references, load_mapping, render_markdown


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_etl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("mapping-check", help="validate the mapping spec")
    p_doc = sub.add_parser("mapping-doc", help="print or write the Markdown view of the mapping")
    p_doc.add_argument("--write", action="store_true")
    p_s1 = sub.add_parser("stage1", help="load and clean the raw CSV files")
    p_s1.add_argument("--out", type=Path, default=DEFAULT_OUT)
    p_s2 = sub.add_parser("stage2", help="build the topology (runs stage 1 in memory)")
    p_s2.add_argument("--out", type=Path, default=topology.DEFAULT_OUT)
    p_s3 = sub.add_parser("stage3", help="build station lines and platforms (runs stage 1 in memory)")
    p_s3.add_argument("--out", type=Path, default=topology.DEFAULT_OUT)
    p_wtt = sub.add_parser("wtt", help="import the WTT (runs stage 1 in memory)")
    p_wtt.add_argument("--out", type=Path, default=topology.DEFAULT_OUT)
    p_fr = sub.add_parser("freight", help="freight demand + historical stats (runs stages 1-2 in memory)")
    p_fr.add_argument("--out", type=Path, default=topology.DEFAULT_OUT)
    p_geo = sub.add_parser("geo-fetch", help="download / verify the OSM and DEM inputs (issue #14)")
    p_geo.add_argument("--update", action="store_true", help="record new hashes in inputs.yaml")
    p_gr = sub.add_parser("gradients", help="derive alignment, gradients and curves (issue #14)")
    p_gr.add_argument("--out", type=Path, default=gradients.OUT_DIR)
    args = ap.parse_args(argv)
    mapping = load_mapping()

    if args.cmd == "mapping-check":
        problems = check(mapping) + ignored_references(mapping)
        for p in problems:
            print(f"FAIL  {p}")
        print("mapping:", "FAILED" if problems else "OK", f"({len(mapping.tables)} tables)")
        return 1 if problems else 0
    if args.cmd == "mapping-doc":
        doc = render_markdown(mapping)
        if args.write:
            MAPPING_DOC.parent.mkdir(parents=True, exist_ok=True)
            MAPPING_DOC.write_text(doc, encoding="utf-8", newline="\n")
            print(f"wrote {MAPPING_DOC.relative_to(REPO)}")
        else:
            sys.stdout.write(doc)
        return 0

    if args.cmd == "geo-fetch":
        problems = geo.fetch(update=args.update)
        for p in problems:
            print(f"FAIL  {p}")
        print("geo inputs:", "FAILED" if problems else "OK", f"(cache: {geo.cache_dir()})")
        return 1 if problems else 0
    if args.cmd == "gradients":
        result, rep_g = gradients.run()
        for path in gradients.write(result, rep_g, args.out):
            print(f"wrote {path}")
        print(f"gradients: {'OK' if rep_g.ok else 'FAILED'} clipped={rep_g.clipped} curves={rep_g.curves}")
        return 0 if rep_g.ok else 1
    if args.cmd == "stage3":
        tables, s1 = run_stage1(mapping, write=False)
        lines, platforms, rep3 = stations.build_station_lines(tables, mapping)
        stations.write_station_lines(lines, platforms, rep3, args.out)
        print(rep3.model_dump_json(indent=1))
        print(f"stage3: {'OK' if s1.ok and rep3.ok else 'FAILED'} -> {args.out}")
        return 0 if s1.ok and rep3.ok else 1
    if args.cmd == "wtt":
        tables, s1 = run_stage1(mapping, write=False)
        tt, rep5 = timetable.build_timetable(tables, mapping)
        timetable.write_timetable(tt, rep5, args.out)
        print(rep5.model_dump_json(indent=1))
        print(f"wtt: {'OK' if s1.ok and rep5.ok else 'FAILED'} -> {args.out}")
        return 0 if s1.ok and rep5.ok else 1
    if args.cmd == "freight":
        tables, s1 = run_stage1(mapping, write=False)
        topo, rep2 = topology.build_topology(tables, mapping)
        demand, rep6 = freight.build_freight(tables, topo, mapping)
        freight.write_freight(demand, rep6, args.out)
        print(rep6.model_dump_json(indent=1, exclude={"loads_per_day_by_block"}))
        return 0 if s1.ok and rep2.ok else 1
    if args.cmd == "stage2":
        tables, s1 = run_stage1(mapping, write=False)
        topo, rep = topology.build_topology(tables, mapping)
        topology.write_topology(topo, rep, args.out)
        print(rep.model_dump_json(indent=1))
        print(f"stage2: {'OK' if s1.ok and rep.ok else 'FAILED'} hash={topo.content_hash()} -> {args.out}")
        return 0 if s1.ok and rep.ok else 1

    _, report = run_stage1(mapping, out_dir=args.out)
    for t in report.tables.values():
        status = "OK " if t.ok else "BAD"
        print(
            f"{status} {t.name:<16} raw={t.rows_raw:>6} blank={t.blank_rows} header={t.header_rows} "
            f"dup={t.duplicate_rows} clean={t.rows_clean:>6} expected={t.expected_rows:>6} "
            f"date_errors={sum(t.date_parse_errors.values())} open_ended={t.open_ended_dates}"
        )
    if report.freight:
        print(f"freight: {report.freight.model_dump()}")
    print(f"stage1: {'OK' if report.ok else 'FAILED'} -> {args.out}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
