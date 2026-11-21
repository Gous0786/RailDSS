# Decision orchestrator (DS-9, issue #84)

`python -m raildss_orchestrator run --date 2025-08-20 --from-h 8 --to-h 9 [--kpis] [--out run.json]`
runs the rolling loop on a real twin day, headless, on a simulated clock.

Each tick: the twin's observed feeds go through ingestion, state, ETAs and conflict detection (#70-#73).
A re-plan is due on a trigger (`opt.replan_triggers`: new conflict, disruption announced, controller
action, order declined) and at least every `opt.replan_max_interval_min` (OD-25); decisions within
`opt.freeze_window_min` and orders already issued are frozen. A re-plan builds the greedy plan (#75) and
a CP-SAT solution warm-started with the greedy schedule (#76, `--alternatives N` adds other seeds), and
checks each option: G0 (state sanity, before the solve and before execution), G1 (solver feasibility),
G2 (rules) and G3 (sandbox replay, which also gives the KPI delta against BL-0). Per detected conflict at
most three verified options, ranked by that delta, enter the HITL lifecycle (#82); the baseline travels
with them. Headless, the best option is accepted, the executor (#83) issues the orders, the station
masters acknowledge them and the twin is re-simulated under them. `--kpis` ends with the day's KPIs, DSS
arm against BL-0 (#81).

**CP-SAT as the primary planner** (`--planner cpsat`, issue #152, FR-OPT-07). By default the greedy plan
carries the rolling state (what the next re-plan freezes). With `--planner cpsat` the verified CP-SAT plan
carries it instead:

- **Freeze.** Frozen decisions and accepted orders are hard constraints of the model (their station
  operation is used and ends within `opt.stability.freeze_tolerance_s`). The G3 replay imposes them
  exactly.
- **Stability.** The previous plan's other decisions in the window are kept by a penalty in the objective
  (`opt.stability`: `per_s` per second a departure moves, `drop` when its station line changes).
- **Never worse than greedy.** When the greedy hint keeps the frozen decisions, the delay objective is
  capped by the hint's (DSC-07).
- **Fallback.** Greedy carries the state when CP-SAT has no verified plan in time. Greedy then imposes the
  frozen decisions as holds and lines, since they come from another planner's plan.
- **One plan to freeze.** Once an option is accepted and its orders are issued, its realised departures
  become the plan the next cycle freezes. Frozen decisions are one per train and station, and an accepted
  order wins over the last plan.

The replay report adds the cycles carried by CP-SAT, the CP-SAT plans verified against those solved, the
plans no worse than greedy, the frozen decisions imposed, and the plan churn (decisions of the previous
plan changed by more than `opt.stability.churn_s`). See `docs/kpi/replay-cpsat-ktv-psa-2025-08-20.md`.

**Disruptions the planner models** (issue #155):
- **Plans and G3.** Announced incidents travel with every plan in `PlanContext.incidents`, as injector disruptions: a speed restriction (TSR), caution working after a signal failure (with a paper-line-clear wait for a block-instrument failure), and a station line out of use (a point or loop failure). The greedy plan, the G3 replay and the day's KPI arms run them through the injector, as the ground truth does.
- **CP-SAT.** The IR (`displib.effects_of`) lengthens the block operations of a restricted line, drops an out-of-use station line, and raises a late train's entry bound.
- **Late entries.** Known late entries (`entry_delays`) are directives of every run.
- **Recovery.** The CP-SAT objective weighs each train's delay at the section exit `opt.recovery.exit_weight` times its class weight, pulling trains back to the booked plan.
- **Scenarios.** `python -m raildss_orchestrator scenarios SCN-02 SCN-03 SCN-06 SCN-07 --report ...` runs the loop with two arms: BL-1 (greedy planner) and DSS (CP-SAT rolling horizon). Each is compared with BL-0 under the same disruptions; see `docs/kpi/scenarios-disruption-loop.md`.

Limits: the planner forks the twin, so its state equals the DSS estimate only while nothing diverges; the
CP-SAT instance does not model closures (a plan that uses a closed line fails G3); CP-SAT needs about
6 s to build its model, so `opt.time_limit_s.routine` (15 s) leaves it about 9 s to improve the hint.
