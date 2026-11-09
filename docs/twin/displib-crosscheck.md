# DISPLIB cross-check of the solver IR (issue #74, DR-0002 F16)

**Source.** *DISPLIB: Train Dispatching benchmark library*, file format specification dated
2025-09-17 (`displib_spec_2025-09-17.pdf`, linked from <https://displib.github.io/>), §1.1–1.3. It was
read for learning only. No DISPLIB instance, solution or verifier is copied into this repository.

**Our side.** `raildss_twin.displib` (the IR, `Instance`) and `Instance.to_displib()` (the exported
document). The IR keeps a few conveniences, such as named operations, several first and last
operations, and capacities. `to_displib()` converts them into the DISPLIB form below.

## Fields

| DISPLIB 2025 | IR before #74 | Now |
|---|---|---|
| Top level `{"trains": [...], "objective": [...]}` | same | same |
| Train = list of operations, referenced by zero-based index | same | same |
| `start_lb` (optional, default 0, non-negative integer) | integer seconds since the start of the day | same, always written |
| `start_ub` (optional, default infinity) | window horizon | same, always written |
| `min_duration` (non-negative integer) | rounded down (a relaxation of < 1 s) | same |
| `resources`: list of `{"resource": str, "release_time": int (optional, default 0)}` | same | same |
| `successors`: **list of integer indices**, non-empty except at the exit | list of `{"operation", "delay"}` objects | **plain indices**. `delay` is always 0 and is rejected if non-zero |
| Exactly **one entry operation** (no predecessors, index 0) and **one exit operation** (no successors, last); operations in topological order | several first operations (one per permitted line) and several last ones | **synthetic entry** at index 0 when a train has alternative first operations; **synthetic exit** (no resources, `min_duration` 0) after the last ones |
| Objective component `{"type": "op_delay", "train", "operation", "threshold", "coeff", "increment"}`; `coeff` and `increment` non-negative integers | no `type`; `coeff` as a float | `type: "op_delay"`; constants written as integers. A non-integer constant is rejected |
| Cost `c · max(0, t − t̄) + d · H(t − t̄)`, 0 if the operation is not on the chosen path | same | same (the evaluator in `displib_check`) |
| Resources are exclusive (one train at a time) | `capacity` field (all 1 in KTV-PSA) | capacity > 1 is rejected on export |
| Solution `{"objective_value", "events": [{"time", "train", "operation"}]}` in a global order | `Schedule` dict (train → path of `(operation, start)`) | unchanged. A DISPLIB solution writer is left for FR-TWIN-19 (P2) |

## Semantics

- **Operation end.** In DISPLIB, an operation ends when the train's next operation starts. That
  duration must be at least `min_duration`. The IR uses the same rule: `s_next >= s + min_duration`.
- **Resource occupation.** A resource is held from the operation's start until its end plus the
  resource's `release_time`. Two operations of different trains on the same resource must not
  overlap in that sense. The IR and the evaluator use the same rule. Operations of the same train
  may share a resource.
- **Exit operation.** A DISPLIB exit operation has no end event, so its resources are *never*
  released. In the IR, the last operation (a station line at the exit) holds its line until
  `s + min_duration + release_time`. That is why the export adds a resource-free synthetic exit
  after it. This difference was the one real semantic gap, and the export now closes it.
- **Ties.** In DISPLIB the global order of events matters at equal times: a resource can pass from
  one train to another at the same instant only if the first train's end event comes first. Our
  evaluator sorts releases before acquisitions at the same time, which is the same rule.
- **Trains already in the network.** The specification's example (§1.3) puts a train that already
  occupies a track as an entry operation pinned in time on that resource. Rolling windows now do the
  same (below).

## Rolling windows (frozen decisions)

`build_instance(..., snapshot=...)` takes a `Snapshot` at the window start: what each train occupies
and since when. This is the part of the DSS state `S_t` the IR needs (#71 will publish `S_t`).
`snapshot_from_occupancy` builds the snapshot from any TDS occupancy log. Trains are handled as
follows:

- **Already running.** The train's graph starts at the operations it occupies, in order (the line or
  sub-block its head is on, plus the one its tail is still clearing). Each is pinned to the second it
  was taken (`start_lb = floor`, `start_ub = ceil`). The alternatives behind the head are removed,
  and the rest of the path stays free.
- **Booked to enter before the start but not yet entered.** The train starts free.
- **Already left.** The train is dropped.

The objective components of operations that were pruned are dropped.

`tests/twin/test_displib_window.py` builds a 12:00–14:00 window on 20 Aug 2025 from such a snapshot.
The independent evaluator accepts the twin's schedule for it, and the exported document follows the
2025 structure rules above.
