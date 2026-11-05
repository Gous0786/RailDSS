"""Command line for the twin.

python -m raildss_twin calibrate [--write]   # running-time calibration (issue #16)
"""

from __future__ import annotations

import argparse
import sys

from raildss_twin import calibration


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m raildss_twin")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_cal = sub.add_parser("calibrate", help="calibrate running times against the freight log and the WTT")
    p_cal.add_argument("--write", action="store_true", help="write the calibration file and the report")
    args = ap.parse_args(argv)

    cal, report = calibration.run()
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
