"""Public geo inputs for gradients and curves: OSM rail extract and Copernicus GLO-30 (issue #14).

The inputs are listed in ``data/external/osm-dem/inputs.yaml`` with their URL (and, for OSM,
the Overpass query), retrieval date and SHA-256. Downloads are cached outside the repository
(``$RAILDSS_CACHE``, default ``%LOCALAPPDATA%/raildss-cache`` or ``~/.cache/raildss``); only the
derived outputs are committed (licences: DR-0001 §5, ``data/external/osm-dem/PROVENANCE.md``).

* ``fetch()`` downloads missing inputs and verifies every cached file against the manifest.
  ``update=True`` records new hashes instead (deliberate refresh of the inputs).
* For OSM, the hash covers the canonical element list (sorted, compact JSON), not the raw response:
  Overpass adds a server timestamp header that changes on every request.
* ``Dem`` reads GLO-30 Cloud-Optimised GeoTIFF tiles. ``tifffile`` parses the TIFF structure; the
  DEFLATE + floating-point-predictor tiles are decoded here with numpy (no ``imagecodecs``).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import time
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Any

import numpy as np
import tifffile
import yaml
from pydantic import BaseModel

from raildss_etl.mapping import REPO

INPUTS = REPO / "data" / "external" / "osm-dem" / "inputs.yaml"
USER_AGENT = "RailDSS/0.1 (research; https://github.com/Gous0786/RailDSS)"
FLOAT_BYTES = 4
CHUNK = 1 << 20


class GeoInput(BaseModel):
    name: str
    kind: str  # "overpass" | "http"
    url: str
    endpoints: list[str] = []  # overpass mirrors tried in order after ``url``
    query: str | None = None
    file: str
    sha256: str | None = None
    bytes: int | None = None
    retrieved: str | None = None


class GeoManifest(BaseModel):
    note: str = ""
    inputs: list[GeoInput]


def cache_dir() -> Path:
    env = os.environ.get("RAILDSS_CACHE")
    if env:
        return Path(env)
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) / "raildss-cache" if base else Path.home() / ".cache" / "raildss"


def load_manifest(path: Path = INPUTS) -> GeoManifest:
    return GeoManifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_manifest(manifest: GeoManifest, path: Path = INPUTS) -> None:
    header = (
        "# Geo inputs for gradients and curves (issue #14). Written by\n"
        "# `python -m raildss_etl geo-fetch --update`; verified by `geo-fetch`.\n"
        "# Licences and attribution: PROVENANCE.md in this directory.\n"
    )
    body = yaml.safe_dump(manifest.model_dump(), sort_keys=False, allow_unicode=True, width=200)
    path.write_text(header + body, encoding="utf-8", newline="\n")


def canonical_osm(raw: bytes) -> bytes:
    elements = json.loads(raw)["elements"]
    elements.sort(key=lambda e: (e["type"], e["id"]))
    return json.dumps(elements, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(item: GeoInput, path: Path) -> str:
    if item.kind == "overpass":
        return hashlib.sha256(canonical_osm(path.read_bytes())).hexdigest()
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(item: GeoInput, dest: Path, attempts: int = 3) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    urls = [item.url, *item.endpoints]
    errors: list[str] = []
    for attempt in range(attempts):
        for url in urls:
            data = None
            if item.kind == "overpass":
                data = urllib.parse.urlencode({"data": item.query or ""}).encode()
            req = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    body = resp.read()
                if item.kind == "overpass":
                    json.loads(body)["elements"]
                dest.write_bytes(body)
                return
            except Exception as exc:  # network errors and busy Overpass servers
                errors.append(f"{url}: {exc}")
        time.sleep(30 * (attempt + 1))
    raise RuntimeError(f"{item.name}: download failed: {errors[-len(urls) :]}")


def fetch(update: bool = False, manifest_path: Path = INPUTS) -> list[str]:
    """Ensure every input is cached; return problems (hash mismatches), or record hashes."""
    manifest = load_manifest(manifest_path)
    problems: list[str] = []
    for item in manifest.inputs:
        path = cache_dir() / item.file
        if not path.exists():
            _download(item, path)
            if update:
                item.retrieved = dt.date.today().isoformat()
        sha = digest(item, path)
        if update:
            item.sha256, item.bytes = sha, path.stat().st_size
        elif item.sha256 != sha:
            problems.append(f"{item.name}: sha256 {sha} != manifest {item.sha256} ({path})")
    if update:
        save_manifest(manifest, manifest_path)
    return problems


def cached(name: str, manifest_path: Path = INPUTS) -> Path:
    for item in load_manifest(manifest_path).inputs:
        if item.name == name:
            return cache_dir() / item.file
    raise KeyError(name)


def decode_float_predictor(raw: bytes, rows: int, cols: int) -> np.ndarray:
    """Undo TIFF predictor 3 (byte-wise horizontal differencing of big-endian byte planes)."""
    b = np.frombuffer(raw, np.uint8).reshape(rows, cols * FLOAT_BYTES)
    b = np.cumsum(b, axis=1, dtype=np.uint8)
    planes = b.reshape(rows, FLOAT_BYTES, cols).transpose(0, 2, 1).copy()
    return planes.view(">f4").reshape(rows, cols).astype(np.float32)


class DemTile:
    """One GLO-30 COG tile; 1024 x 1024 internal tiles are decoded lazily."""

    def __init__(self, path: Path) -> None:
        self.path = path
        with tifffile.TiffFile(path) as tif:
            page = tif.pages[0]
            assert isinstance(page, tifffile.TiffPage)
            tie = page.tags["ModelTiepointTag"].value
            scale = page.tags["ModelPixelScaleTag"].value
            self.lon0, self.lat0 = float(tie[3]), float(tie[4])
            self.dx, self.dy = float(scale[0]), float(scale[1])
            self.height, self.width = int(page.shape[0]), int(page.shape[1])
            self.tw, self.tl = int(page.tilewidth), int(page.tilelength)
            self.offsets = list(page.dataoffsets)
            self.counts = list(page.databytecounts)
            if page.predictor != 3 or page.compression != 8:
                raise ValueError(f"{path.name}: unsupported encoding")
        self.tiles_x = -(-self.width // self.tw)
        self._cache: dict[int, np.ndarray] = {}

    def contains(self, lat: float, lon: float) -> bool:
        col = (lon - self.lon0) / self.dx
        row = (self.lat0 - lat) / self.dy
        return 0 <= col <= self.width and 0 <= row <= self.height

    def _pixel(self, row: int, col: int) -> float:
        idx = (row // self.tl) * self.tiles_x + col // self.tw
        if idx not in self._cache:
            with self.path.open("rb") as fh:
                fh.seek(self.offsets[idx])
                raw = zlib.decompress(fh.read(self.counts[idx]))
            self._cache[idx] = decode_float_predictor(raw, self.tl, self.tw)
        return float(self._cache[idx][row % self.tl, col % self.tw])

    def elevation(self, lat: float, lon: float) -> float:
        """Bilinear interpolation between pixel centres (pixel-is-area: centre at +0.5)."""
        col = (lon - self.lon0) / self.dx - 0.5
        row = (self.lat0 - lat) / self.dy - 0.5
        c0 = min(max(math.floor(col), 0), self.width - 2)
        r0 = min(max(math.floor(row), 0), self.height - 2)
        fc, fr = min(max(col - c0, 0.0), 1.0), min(max(row - r0, 0.0), 1.0)
        z00, z01 = self._pixel(r0, c0), self._pixel(r0, c0 + 1)
        z10, z11 = self._pixel(r0 + 1, c0), self._pixel(r0 + 1, c0 + 1)
        return (z00 * (1 - fc) + z01 * fc) * (1 - fr) + (z10 * (1 - fc) + z11 * fc) * fr


class Dem:
    def __init__(self, paths: list[Path]) -> None:
        self.tiles = [DemTile(p) for p in paths]

    def elevation(self, lat: float, lon: float) -> float:
        for tile in self.tiles:
            if tile.contains(lat, lon):
                return tile.elevation(lat, lon)
        raise ValueError(f"no DEM tile covers {lat:.5f}, {lon:.5f}")


def load_osm(path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_bytes())
    return data
