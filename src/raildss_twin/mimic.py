"""Schematic mimic data for the web UI (spec FR-UI-06; issue #87).

The mimic is generated, not drawn: ``mimic_data`` exports the layout of the published network and the
occupancy of a twin run, and the UI draws a straight-line schematic from them.

* **stations**: every operational point by chainage, with its station lines (number, name, kind, usable
  length = CSR, platform, the directions it serves, in use or not);
* **blocks**: one per section between adjacent block stations (its DN and UP COA blocks share the
  lines), each line with its direction,
  block system and number of automatic sub-blocks; the resource keys match the twin's occupancy labels;
* **signals**: the twin's signals (synthesised, R06 §4.4) with chainage, line, direction and type;
* **occupancy**: who held which resource when, from the twin's occupancy log. ``resources`` lists the
  labels (``SL:<op>/<line>``, ``BL:<segment>/<line>``, ``SB:<segment>/<line>#<n>``) and ``intervals`` the
  ``[resource index, train index, start s, end s]`` of each occupation (``end`` = null while still held at
  the end of the run). Train indices refer to the chart's ``trains`` list.

It is a supervisory view only: there is no route setting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from raildss_schema.entities import Signal
from raildss_twin import run
from raildss_twin.layout import pair_key
from raildss_twin.sim import Sim

KM_DIGITS = 3  # metres


def _stations(world: run.World) -> list[dict[str, Any]]:
    out = []
    for code in world.layout.order:
        op = world.layout.ops[code]
        out.append(
            {
                "code": code,
                "name": op.name,
                "km": round(op.km, KM_DIGITS),
                "halt": op.is_halt,
                "lines": [
                    {
                        "no": sl.line_no,
                        "name": sl.name,
                        "kind": sl.kind,
                        "csr_m": sl.csr_m,
                        "platform": sl.platform,
                        "directions": sl.directions,
                        "in_use": sl.status == "in_use",
                    }
                    for sl in world.layout.station_lines.get(code, [])
                ],
            }
        )
    return out


def _blocks(world: run.World) -> list[dict[str, Any]]:
    layout = world.layout
    out = []
    seen: set[str] = set()
    for block_id, block in sorted(layout.blocks.items(), key=lambda kv: (layout.km(kv[1].from_op), kv[0])):
        segment = pair_key(block.from_op, block.to_op)
        if segment in seen:  # the DN and UP COA blocks of one section share its lines
            continue
        seen.add(segment)
        a, b = sorted((block.from_op, block.to_op), key=layout.km)
        out.append(
            {
                "id": block_id,
                "segment": segment,
                "from": a,
                "to": b,
                "from_km": round(layout.km(a), KM_DIGITS),
                "to_km": round(layout.km(b), KM_DIGITS),
                "system": block.system,
                "lines": [
                    {
                        "line_id": ln.line_id,
                        "direction": ln.direction,
                        "sub_blocks": len(layout.canonical_sub_blocks(block_id, ln.line_id))
                        if ln.system == "AUTO"
                        else 0,
                    }
                    for ln in sorted(layout.lines[block_id], key=lambda x: x.line_id)
                ],
            }
        )
    return out


def _signals(world: run.World) -> list[dict[str, Any]]:
    signals: Sequence[Signal] = world.tables.get("signal", [])  # type: ignore[assignment]
    return [
        {
            "id": s.id,
            "type": s.type,
            "km": round(s.location.km, KM_DIGITS) if s.location.km is not None else None,
            "line_id": s.line_id,
            "direction": s.direction,
        }
        for s in sorted(signals, key=lambda s: (s.location.km or 0.0, s.id))
    ]


def _by_start(interval: list[float | int | None]) -> tuple[float, float, float]:
    resource, train, start, _ = interval
    return float(start or 0), float(resource or 0), float(train or 0)


def occupancy(sim: Sim, train_ids: Sequence[str]) -> dict[str, Any]:
    """Occupation intervals from the twin's occupancy log (acquire / release pairs per resource and train)."""
    index = {rid: i for i, rid in enumerate(train_ids)}
    resources: dict[str, int] = {}
    open_: dict[tuple[str, str], list[float]] = {}
    intervals: list[list[float | int | None]] = []
    for t, label, rid, acquired in sim.occupancy_log:
        if rid not in index:
            continue
        key = (label, rid)
        if acquired:
            open_.setdefault(key, []).append(t)
            continue
        starts = open_.get(key)
        if not starts:
            continue
        r = resources.setdefault(label, len(resources))
        intervals.append([r, index[rid], round(starts.pop(0)), round(t)])
    for (label, rid), starts in open_.items():
        for s in starts:
            r = resources.setdefault(label, len(resources))
            intervals.append([r, index[rid], round(s), None])
    intervals.sort(key=_by_start)
    return {"resources": list(resources), "intervals": intervals}


def mimic_data(world: run.World, sim: Sim, train_ids: Sequence[str]) -> dict[str, Any]:
    return {
        "stations": _stations(world),
        "blocks": _blocks(world),
        "signals": _signals(world),
        "occupancy": occupancy(sim, train_ids),
    }
