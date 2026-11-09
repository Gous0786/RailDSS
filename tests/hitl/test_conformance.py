"""Conformance of :mod:`raildss_hitl.lifecycle` with the TLA+ model (FR-HITL-01; issue #82).

CI runs TLC on ``specs/tla/HitlLifecycle.tla`` once per configuration ``specs/tla/<name>.cfg`` with
``-dump dot,actionlabels <dir>/<name>.dot`` and points ``RAILDSS_TLC_DIR`` at ``<dir>``. Every
model state is then rebuilt in the implementation, and from every state the implementation must
allow exactly the model's transitions (same action, same next state), no more and no fewer.
The reachable state spaces are therefore identical.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

import pytest

from tests.hitl.model import CONFIGS, State, bounds, initial, moves, reachable

DOT_DIR = os.environ.get("RAILDSS_TLC_DIR")
pytestmark = pytest.mark.skipif(not DOT_DIR, reason="RAILDSS_TLC_DIR not set (the CI TLA+ job sets it)")
NODE = re.compile(r'^\s*(-?\d+)\s*\[label="((?:[^"\\]|\\.)*)"')
EDGE = re.compile(r'^\s*(-?\d+)\s*->\s*(-?\d+)\s*\[label="((?:[^"\\]|\\.)*)"')
VARS = ("status", "basedOn", "validUntil", "decided", "clock", "ver")


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), text)


def _value(tla: str) -> object:
    py = tla.replace("<<", "(").replace(">>", ",)").replace("TRUE", "True").replace("FALSE", "False")
    return ast.literal_eval(py)


def parse_state(label: str) -> State:
    values: dict[str, object] = {}
    for raw in _unescape(label).splitlines():
        line = raw.strip().removeprefix("/\\").strip()
        if " = " in line:
            name, _, value = line.partition(" = ")
            values[name.strip()] = _value(value.strip())
    missing = set(VARS) - values.keys()
    assert not missing, f"variables {missing} not in node label {label!r}"
    return tuple(values[v] for v in VARS)  # type: ignore[return-value]


def parse_dot(path: Path) -> tuple[dict[str, State], dict[str, set[tuple[str, str]]]]:
    nodes: dict[str, State] = {}
    edges: dict[str, set[tuple[str, str]]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if m := EDGE.match(line):
            src, dst, label = m.groups()
            if src != dst:
                edges.setdefault(src, set()).add((_unescape(label).split("(")[0].strip(), dst))
        elif (m := NODE.match(line)) and "status" in m.group(2):
            nodes[m.group(1)] = parse_state(m.group(2))
    return nodes, edges


def _dump(cfg: Path) -> Path:
    return Path(str(DOT_DIR)) / f"{cfg.stem}.dot"


@pytest.mark.parametrize("cfg", CONFIGS, ids=lambda p: p.stem)
def test_the_implementation_allows_exactly_the_model_transitions(cfg: Path) -> None:
    b = bounds(cfg)
    nodes, edges = parse_dot(_dump(cfg))
    assert len(nodes) > 50 and edges, "the TLC dump looks empty"
    assert initial(b) in set(nodes.values())
    mismatches = []
    for node, state in nodes.items():
        model = {(label, nodes[dst]) for label, dst in edges.get(node, set())}
        impl = moves(state, b)
        if model != impl:
            mismatches.append((state, sorted(model - impl), sorted(impl - model)))
    assert not mismatches, f"{len(mismatches)} states differ, first: {mismatches[0]}"


@pytest.mark.parametrize("cfg", CONFIGS, ids=lambda p: p.stem)
def test_the_reachable_state_spaces_are_identical(cfg: Path) -> None:
    b = bounds(cfg)
    nodes, _ = parse_dot(_dump(cfg))
    assert set(reachable(b)) == set(nodes.values())
