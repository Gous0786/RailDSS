"""SHA-256 manifests for committed datasets (spec FR-DATA-03, C-13; issue #2).

Each dataset directory holds a ``MANIFEST.sha256`` in ``sha256sum`` format
(``<hex digest>  <relative/path>``), one line per data file, sorted by path.
``MANIFEST.sha256`` and ``PROVENANCE.md`` themselves are not listed.

    python tools/manifest.py --check            # verify all dataset dirs (CI)
    python tools/manifest.py --write DIR [DIR]  # (re)write the manifest for DIR

``--write`` refuses to overwrite a manifest under data/raw/ when a recorded file has
changed: raw data is immutable input (C-13). Use ``--force`` only for a reviewed,
deliberate replacement of the raw extract.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATASET_DIRS = [ROOT / "data" / "raw" / "ktv-psa", ROOT / "data" / "derived" / "ktv-psa"]
MANIFEST = "MANIFEST.sha256"
EXCLUDED = {MANIFEST, "PROVENANCE.md"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def data_files(directory: Path) -> list[Path]:
    return sorted(
        (p for p in directory.rglob("*") if p.is_file() and p.name not in EXCLUDED),
        key=lambda p: p.relative_to(directory).as_posix(),
    )


def compute(directory: Path) -> dict[str, str]:
    return {p.relative_to(directory).as_posix(): sha256(p) for p in data_files(directory)}


def read_manifest(directory: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in (directory / MANIFEST).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        entries[rel] = digest
    return entries


def diff(directory: Path) -> list[str]:
    """Return human-readable problems; empty list means the manifest matches."""
    if not (directory / MANIFEST).exists():
        return [f"{directory}: {MANIFEST} is missing"]
    recorded, actual = read_manifest(directory), compute(directory)
    problems = [f"{directory.name}/{r}: listed but missing" for r in sorted(recorded.keys() - actual.keys())]
    problems += [f"{directory.name}/{r}: not in manifest" for r in sorted(actual.keys() - recorded.keys())]
    problems += [
        f"{directory.name}/{r}: hash changed"
        for r in sorted(recorded.keys() & actual.keys())
        if recorded[r] != actual[r]
    ]
    return problems


def write(directory: Path, force: bool) -> None:
    is_raw = "raw" in directory.relative_to(ROOT).parts if directory.is_relative_to(ROOT) else False
    if is_raw and (directory / MANIFEST).exists() and not force:
        changed = [
            p for p in diff(directory) if p.endswith("hash changed") or p.endswith("listed but missing")
        ]
        if changed:
            raise SystemExit(
                "refusing to rewrite a raw-data manifest with changed or removed files "
                "(C-13):\n  " + "\n  ".join(changed) + "\nUse --force for a reviewed replacement."
            )
    lines = [f"{digest}  {rel}" for rel, digest in compute(directory).items()]
    (directory / MANIFEST).write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {directory / MANIFEST} ({len(lines)} files)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="verify all dataset manifests")
    mode.add_argument("--write", nargs="+", type=Path, metavar="DIR", help="write manifest for DIR(s)")
    ap.add_argument("--force", action="store_true", help="allow rewriting a raw manifest after review")
    args = ap.parse_args(argv)
    if args.write:
        for d in args.write:
            write(d.resolve(), args.force)
        return 0
    problems = [p for d in DATASET_DIRS for p in diff(d)]
    for p in problems:
        print(f"FAIL  {p}")
    print("manifest check:", "FAILED" if problems else f"OK ({len(DATASET_DIRS)} dataset dirs)")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
