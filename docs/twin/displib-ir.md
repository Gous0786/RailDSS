# Solver-facing IR, DISPLIB-style (issue #22)

`python -m raildss_twin displib --date 2025-08-20 --from-h 0 --to-h 2` converts the trains that enter in a
window of a day into an `Instance` (`src/raildss_twin/displib.py`) and writes two files under
`data/interim/ktv-psa/displib/` (not committed): `<scenario>.ir.json` (the IR with metadata) and
`<scenario>.displib.json` (the plain DISPLIB-structured view: `trains` and `objective`).

Spec: FR-OPT-01 (solver-facing IR), FR-TWIN-19 (P2), REC-02, OD-17, A-07.

## Mapping

| IR | Built from |
|---|---|
| Train | a passenger plan (WTT for the date, booked times) or a freight load of the day instance (entry point and time; the solver decides the movement) |
| Operation, `station` | one train at one station line; one alternative per permitted line (main or loop, in use, passenger or goods) |
| Operation, `block` | one train in one absolute-block line (VZM-PSA), one alternative per permitted line |
| Operation, `sub_block` | one train in one synthetic sub-block of an automatic-block line (KTV-VZM), a chain per line |
| Edge (successor) | only where a Connections edge permits it: receive (block line to station line) and send (station line to block line) |
| Resource | station line, block line or sub-block; capacity 1 unless the data says otherwise. The reversible MID line is one resource for both directions |
| `min_duration` | calibrated running time of the block (issue #16), split over sub-blocks by length; WTT stop: minimum dwell; exit: exit clearing time |
| `start_lb` | entry time; for a booked train, the booked departure on the block operation after a stop and the booked arrival at a non-stop point |
| `release_time` | clearing time of the line: (train length + adequate distance) over the clearing speed (`twin.sim`) |
| Objective | passenger: weight x arrival delay against the booked time at every stop and at the exit; freight: weight x delay against unimpeded completion; weights from `policy.weights` |

## Semantics

Along an edge `s_next >= s + min_duration + delay`. An operation occupies its resources from `s` until
the next operation's start plus `release_time`; the last operation until `s + min_duration + release_time`.
Different trains must not overlap on a resource beyond its capacity. Times are integer seconds since the
start of the day; durations and releases are rounded down, so the IR relaxes the twin by less than one
second per operation. Not in the IR: halt departure times inside a block, the reversible-line direction
gate and clear-standing-room (CSR) checks; the twin enforces the first two, and CSR is M1.

## Checking

`raildss_twin.displib_check.evaluate` is an independent evaluator over the IR (paths follow edges, bounds,
durations, resource exclusivity, objective). Tests run it on a serial schedule and on the BL-0 twin's choices
for the same window (`schedule_from_trace`).

## Window of 20 Aug 2025, 00:00 to 02:00 (18 trains)

| Count | Value |
|---|---|
| Resources | 223 |
| Operations | 1,811 |
| Edges | 2,508 |
| Objective components | 233 |

## DISPLIB status

The DISPLIB verifier, instances and solutions are **not used, vendored or committed**: their licence is
unstated (DR-0001 D5, OD-17) and the request to SINTEF is drafted, awaiting the user to send. The field names
follow the published description as recalled when this was written and must be checked against
`displib_spec_2025-09-17.pdf` before P2 (FR-TWIN-19: "the DISPLIB verifier accepts the exported instance").
Acceptance criterion 3 of M0-22 ("if the verifier is available locally ...") is therefore not run; the
internal evaluator above stands in for it.
