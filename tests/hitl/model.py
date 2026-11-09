"""The bounded world of ``specs/tla/HitlLifecycle.tla`` driven through :class:`Lifecycle` (issue #82).

``moves`` lists every transition the implementation allows from a model state, labelled with the
model action it performs. The conformance test compares it with the TLC state graph; the
exhaustive test explores it locally and checks the model's invariants.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from raildss_hitl.lifecycle import (
    AUTHORITY,
    COMMITTED,
    OPEN,
    Lifecycle,
    Recommendation,
    Status,
    TransitionError,
)

ROOT = Path(__file__).resolve().parents[2]
SPECS = ROOT / "specs" / "tla"
CONFIGS = sorted(SPECS.glob("*.cfg"))  # one bounded world per TLC configuration
NONE = "NONE"
BLOCKS = frozenset({1})  # Block(c) == 1: every context acts on the same block

# (status per rec, basedOn, validUntil, decided, clock, ver): the model's variables
State = tuple[tuple[str, ...], tuple[int, ...], tuple[int, ...], tuple[bool, ...], int, int]


@dataclass(frozen=True)
class Bounds:
    n_recs: int
    max_time: int
    max_ver: int
    validity: int
    same_context: bool

    @property
    def recs(self) -> range:
        return range(1, self.n_recs + 1)

    def ctx(self, r: int) -> int:
        return 1 if self.same_context else r


def bounds(path: Path) -> Bounds:
    found = dict(re.findall(r"^\s*(\w+)\s*=\s*(\w+)\s*$", path.read_text(encoding="utf-8"), re.M))
    return Bounds(
        int(found["NRecs"]),
        int(found["MaxTime"]),
        int(found["MaxVer"]),
        int(found["Validity"]),
        found["SameContext"] == "TRUE",
    )


def to_state(lc: Lifecycle, b: Bounds) -> State:
    recs = [lc.recs.get(r) for r in b.recs]
    return (
        tuple(r.status.value if r else NONE for r in recs),
        tuple(r.based_on if r else 0 for r in recs),
        tuple(int(r.valid_until) if r else 0 for r in recs),
        tuple(r.decided if r else False for r in recs),
        int(lc.now),
        lc.state_version,
    )


def from_state(s: State, b: Bounds) -> Lifecycle:
    status, based_on, valid_until, decided, clock, ver = s
    lc = Lifecycle(now=clock, state_version=ver)
    for i, r in enumerate(b.recs):
        if status[i] != NONE:
            lc.recs[r] = Recommendation(
                r, b.ctx(r), BLOCKS, valid_until[i], based_on[i], Status(status[i]), decided[i]
            )
    return lc


Step = Callable[[Lifecycle], str | None]  # returns a label override, or None


def _candidates(s: State, b: Bounds) -> list[tuple[str, Step]]:
    status, *_, clock, ver = s
    out: list[tuple[str, Step]] = []
    if clock < b.max_time:
        out.append(("Tick", lambda lc: lc.advance(lc.now + 1)))
    if ver < b.max_ver:
        out.append(("StateChange", lambda lc: lc.state_changed()))
    for i, r in enumerate(b.recs):
        earlier_free = any(status[j - 1] == NONE for j in b.recs if j < r and b.ctx(j) == b.ctx(r))
        if status[i] == NONE and not earlier_free:  # the model creates a context's recs in id order
            out.append(
                ("Propose", lambda lc, r=r: lc.propose(r, b.ctx(r), BLOCKS, lc.now + b.validity) and None)
            )
        out += [
            ("Present", lambda lc, r=r: lc.verification_result(r, True)),
            ("VerifyFail", lambda lc, r=r: lc.verification_result(r, False)),
            ("Accept", lambda lc, r=r: lc.accept(r)),
            ("Modify", lambda lc, r=r: lc.modify(r)),
            ("ReverifyPass", lambda lc, r=r: lc.reverified(r, True)),
            ("ReverifyFail", lambda lc, r=r: lc.reverified(r, False)),
            ("Reject", lambda lc, r=r: lc.reject(r, "unsafe")),
            ("Expire", lambda lc, r=r: lc.expire(r)),
            ("PreExecCheck", lambda lc, r=r: lc.pre_exec_check(r)),
            (
                "PreExec",
                lambda lc, r=r: (
                    "PreExecOk" if lc.pre_exec_result(r) is Status.ORDER_ISSUED else "PreExecFail"
                ),
            ),
            ("Acknowledge", lambda lc, r=r: lc.acknowledged(r)),
            ("Decline", lambda lc, r=r: lc.declined(r)),
            ("Replan", lambda lc, r=r: lc.replan(r, lc.now + b.validity)),
            ("Start", lambda lc, r=r: lc.start(r)),
            ("Complete", lambda lc, r=r: lc.completed(r)),
            ("Diverge", lambda lc, r=r: lc.diverged(r)),
        ]
    return out


def moves(s: State, b: Bounds) -> set[tuple[str, State]]:
    """Every (model action, next state) the implementation allows from ``s``; stutters are dropped."""
    out = set()
    for label, step in _candidates(s, b):
        lc = from_state(s, b)
        try:
            override = step(lc)
        except TransitionError:
            continue
        t = to_state(lc, b)
        if t != s:
            out.add((override or label, t))
    return out


def initial(b: Bounds) -> State:
    return to_state(Lifecycle(), b)


# ------------------------------------------------------------------ the model's invariants


def violations(s: State, b: Bounds) -> list[str]:
    status, based_on, _, decided, _, ver = s
    live = OPEN | COMMITTED
    post = COMMITTED | {Status.COMPLETED, Status.DIVERGED}
    out = []
    for i, r in enumerate(b.recs):
        st = status[i]
        for j, q in enumerate(b.recs):
            if q == r:
                continue
            if b.ctx(q) == b.ctx(r) and st in live and status[j] in live:
                out.append(f"OnePerContext {r},{q}")
            if st in AUTHORITY and status[j] in AUTHORITY:  # every pair shares block 1
                out.append(f"NoTwoAuthorities {r},{q}")
        if st in AUTHORITY and based_on[i] != ver:
            out.append(f"NoStaleExecution {r}")
        if st in post and not decided[i]:
            out.append(f"NoExecutionWithoutDecision {r}")
        if st == Status.EXPIRED and decided[i]:
            out.append(f"ExpiredWasNotDecided {r}")
    return out


def reachable(b: Bounds) -> dict[State, set[tuple[str, State]]]:
    """Breadth-first exploration of the implementation from the initial state."""
    graph: dict[State, set[tuple[str, State]]] = {}
    todo = [initial(b)]
    while todo:
        s = todo.pop()
        if s in graph:
            continue
        graph[s] = moves(s, b)
        todo.extend(t for _, t in graph[s] if t not in graph)
    return graph
