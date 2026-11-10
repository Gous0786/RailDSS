"""CP-SAT model of a DISPLIB 2025 problem (spec FR-OPT-01, DS-6 (b); issue #76).

``solve`` takes the solver-facing IR as a DISPLIB document (``Instance.to_displib()``, #74) and
returns a DISPLIB solution, so the independent G1 evaluator (#77) can check it. The model:

* **Paths.** A presence literal per operation and one per successor edge. The entry (index 0) and the
  exit (the last one) are present; a present operation with successors takes exactly one outgoing
  edge, and every present operation but the entry has exactly one incoming edge.
* **Times.** A start per operation within ``[start_lb, start_ub]``. Along a chosen edge the next
  operation starts at least ``min_duration`` later, and the operation ends when it does.
* **Resources.** Each use is an optional interval from the start to the end plus the release time;
  the intervals of a resource do not overlap (DISPLIB resources are exclusive).
* **Objective.** ``op_delay``: ``coeff * max(0, t - threshold) + increment * [t > threshold]``.
* **Hint.** A known feasible schedule (the BL-0 twin's, FR-OPT-03) can warm-start the search.

Events of equal time are ordered so that a train releasing a resource comes before the train that
takes it (the DISPLIB global-order rule).
"""

from __future__ import annotations

import itertools
import time
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from ortools.sat.python import cp_model
from pydantic import BaseModel, Field

Problem = Mapping[str, Any]
Path = list[tuple[int, int]]  # [(operation index, start time)] from entry to exit


class SolveResult(BaseModel):
    status: str
    objective: float | None = None
    bound: float | None = None
    wall_s: float
    first_feasible_s: float | None = None
    trajectory: list[tuple[float, float]] = Field(default_factory=list)  # (wall s, objective) improvements
    solution: dict[str, Any] | None = None
    size: dict[str, int] = Field(default_factory=dict)


class _Progress(cp_model.CpSolverSolutionCallback):
    def __init__(self, t0: float) -> None:
        super().__init__()
        self.t0 = t0
        self.points: list[tuple[float, float]] = []

    def on_solution_callback(self) -> None:
        self.points.append((round(time.perf_counter() - self.t0, 3), self.ObjectiveValue()))


def _horizon(problem: Problem) -> int:
    ubs = [int(op["start_ub"]) for ops in problem["trains"] for op in ops if "start_ub" in op]
    durs = sum(int(op.get("min_duration", 0)) for ops in problem["trains"] for op in ops)
    return max(ubs, default=0) + min(durs, 7 * 24 * 3600) + 1


def paths_from_solution(problem: Problem, solution: Mapping[str, Any]) -> dict[int, Path]:
    out: dict[int, Path] = defaultdict(list)
    for e in solution["events"]:
        out[int(e["train"])].append((int(e["operation"]), int(e["time"])))
    return dict(out)


def order_events(problem: Problem, paths: Mapping[int, Path]) -> list[dict[str, int]]:
    """DISPLIB events in global order: by time, a releasing train before the train that takes the resource."""
    trains = problem["trains"]
    events = []
    for ti, path in paths.items():
        for n, (oi, t) in enumerate(path):
            ended = path[n - 1][0] if n else None
            released = (
                {str(r["resource"]) for r in trains[ti][ended].get("resources", [])}
                if ended is not None
                else set()
            )
            taken = {str(r["resource"]) for r in trains[ti][oi].get("resources", [])}
            events.append((t, ti, oi, released, taken))
    by_time: dict[int, list[Any]] = defaultdict(list)
    for ev in events:
        by_time[ev[0]].append(ev)
    ordered: list[dict[str, int]] = []
    for t in sorted(by_time):
        group = sorted(by_time[t], key=lambda e: (e[1], e[2]))
        after: dict[int, set[int]] = {i: set() for i in range(len(group))}
        for i, a in enumerate(group):
            for j, b in enumerate(group):
                if a[1] != b[1] and a[3] & b[4]:
                    after[j].add(i)  # b takes what a releases: a first
        done: list[int] = []
        left = set(range(len(group)))
        while left:
            ready = sorted(i for i in left if not (after[i] & left)) or sorted(
                left
            )  # a cycle: keep stable order
            done.append(ready[0])
            left.remove(ready[0])
        ordered += [{"time": t, "train": group[i][1], "operation": group[i][2]} for i in done]
    return ordered


def objective_of(problem: Problem, paths: Mapping[int, Path]) -> float:
    starts = {(ti, oi): t for ti, p in paths.items() for oi, t in p}
    total = 0.0
    for c in problem.get("objective", []):
        t = starts.get((int(c["train"]), int(c["operation"])))
        if t is not None:
            thr = float(c.get("threshold", 0))
            total += float(c.get("coeff", 0)) * max(0.0, t - thr) + float(c.get("increment", 0)) * (t > thr)
    return total


def solve(
    problem: Problem,
    *,
    time_limit_s: float,
    workers: int,
    hint: Mapping[str, Any] | None = None,
    seed: int = 0,
) -> SolveResult:
    """Build and solve the CP-SAT model; the result carries a DISPLIB solution when one is found."""
    trains: Sequence[Sequence[Mapping[str, Any]]] = problem["trains"]
    horizon = _horizon(problem)
    m = cp_model.CpModel()
    x: list[list[Any]] = []
    s: list[list[Any]] = []
    end: list[list[Any]] = []
    edges: list[dict[tuple[int, int], Any]] = []
    by_resource: dict[str, list[Any]] = defaultdict(list)
    for ti, ops in enumerate(trains):
        xs, ss, es = [], [], []
        for oi, op in enumerate(ops):
            lb = int(op.get("start_lb", 0))
            ub = min(int(op["start_ub"]), horizon) if "start_ub" in op else horizon
            xs.append(m.NewBoolVar(f"x{ti}_{oi}"))
            ss.append(m.NewIntVar(lb, max(lb, ub), f"s{ti}_{oi}"))
            es.append(m.NewIntVar(0, 2 * horizon, f"e{ti}_{oi}"))
        m.Add(xs[0] == 1)
        m.Add(xs[-1] == 1)
        inc: dict[int, list[Any]] = defaultdict(list)
        ed: dict[tuple[int, int], Any] = {}
        for oi, op in enumerate(ops):
            succ = [int(n) for n in op.get("successors", [])]
            if not succ:
                m.Add(es[oi] == ss[oi] + int(op["min_duration"])).OnlyEnforceIf(xs[oi])
                continue
            out = []
            for nj in succ:
                e = m.NewBoolVar(f"y{ti}_{oi}_{nj}")
                ed[(oi, nj)] = e
                out.append(e)
                inc[nj].append(e)
                m.Add(ss[nj] >= ss[oi] + int(op["min_duration"])).OnlyEnforceIf(e)
                m.Add(es[oi] == ss[nj]).OnlyEnforceIf(e)
            m.Add(sum(out) == xs[oi])
        for oi in range(1, len(ops)):
            m.Add(sum(inc[oi]) == xs[oi])
        for oi, op in enumerate(ops):
            for use in op.get("resources", []):
                rel = int(use.get("release_time", 0))
                size = m.NewIntVar(0, 3 * horizon, f"z{ti}_{oi}_{use['resource']}")
                iv = m.NewOptionalIntervalVar(ss[oi], size, es[oi] + rel, xs[oi], f"i{ti}_{oi}")
                by_resource[str(use["resource"])].append(iv)
        x.append(xs)
        s.append(ss)
        end.append(es)
        edges.append(ed)
    for ivs in by_resource.values():
        if len(ivs) > 1:
            m.AddNoOverlap(ivs)
    terms = []
    for k, c in enumerate(problem.get("objective", [])):
        ti, oi = int(c["train"]), int(c["operation"])
        thr = int(c.get("threshold", 0))
        coeff, incr = int(c.get("coeff", 0)), int(c.get("increment", 0))
        d = m.NewIntVar(0, 2 * horizon, f"d{k}")
        m.Add(d >= s[ti][oi] - thr).OnlyEnforceIf(x[ti][oi])
        terms.append(coeff * d)
        if incr:
            late = m.NewBoolVar(f"l{k}")
            m.Add(s[ti][oi] <= thr).OnlyEnforceIf([x[ti][oi], late.Not()])
            terms.append(incr * late)
    m.Minimize(sum(terms))
    if hint is not None:
        for ti, path in paths_from_solution(problem, hint).items():
            on = {oi for oi, _ in path}
            for oi in range(len(trains[ti])):
                m.AddHint(x[ti][oi], oi in on)
            for oi, t in path:
                m.AddHint(s[ti][oi], t)
            chosen = {(a, b) for (a, _), (b, _) in itertools.pairwise(path)}
            for key, e in edges[ti].items():
                m.AddHint(e, key in chosen)
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed
    t0 = time.perf_counter()
    progress = _Progress(t0)
    status = solver.Solve(m, progress)
    wall = round(time.perf_counter() - t0, 3)
    name = solver.StatusName(status)
    result = SolveResult(
        status=name,
        wall_s=wall,
        trajectory=progress.points,
        first_feasible_s=progress.points[0][0] if progress.points else None,
        size={
            "trains": len(trains),
            "operations": sum(len(o) for o in trains),
            "resources": len(by_resource),
            "booleans": sum(len(xs) for xs in x) + sum(len(e) for e in edges),
        },
    )
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        paths: dict[int, Path] = {}
        for ti in range(len(trains)):
            path, oi = [], 0
            while True:
                path.append((oi, solver.Value(s[ti][oi])))
                nxt = [nj for (a, nj), e in edges[ti].items() if a == oi and solver.Value(e)]
                if not nxt:
                    break
                oi = nxt[0]
            paths[ti] = path
        value = objective_of(problem, paths)
        result.objective = value
        result.bound = solver.BestObjectiveBound()
        result.solution = {"objective_value": value, "events": order_events(problem, paths)}
    return result
