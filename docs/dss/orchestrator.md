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

**Contrasting options and 'why not'** (issue #154, FR-OPT-05/06; register `opt.contrast`):
- **The other precedence.** For the `per_cycle` most urgent decisions released in a cycle, the planner re-solves with the other precedence: the train the best plan serves last at the decision's resource goes first (promoted in every queue). The re-solved plan is verified (G2, G3). When it really reverses the order and passes, it is released as a further option of that decision, ranked by its KPI delta with the others.
- **Cycle record.** It lists per decision the options released and how many different trains they serve first (`decisions`), and each re-solve with its answer (`contrasts`). The replay report counts the decisions with two or more feasible precedences that got options of both.
- **Imposing the precedence** (issue #196). Promoting the train alone left the timetable order wherever nothing contended. The re-solve now holds the trains the best plan serves first at their station until the promoted train has departed plus `opt.contrast.margin_s`. It imposes the frozen decisions of the other trains, and skips decisions whose precedence is already decided (frozen, ordered or under way). Once a contrasting option is accepted, later plans impose the frozen decisions, because the planner does not reproduce that plan by itself. A decision's precedences count the orders already in force with the options released.
- **'Why not X'.** `Orchestrator.why_not(trains, first, resource)` re-solves the last plan's context with `first` served first. It returns the verified plan and its cost in passenger delay-minutes against the best plan, or the rules that forbid it.

**Boundary acceptance windows** (issue #157, FR-OPT-20, SCN-23):
- **Where the windows act.** A closed exit (`exit_blocked` in a scenario, the loop's `exit_windows`) applies to the world, to every plan and to the day's KPI arms.
- **Twin and greedy.** A train whose next hop leads into a closed exit waits at its last block station until the window ends. The twin re-checks the window once the block line is granted: a train that queued for the line while the exit shut gives the line back and waits.
- **CP-SAT.** The IR marks the final-hop operations of such trains with the `closed` windows (rounded inwards, a relaxation of the twin by less than a second). CP-SAT places each operation wholly before or after a window, and G1 checks it.
- **Verification.** VER-EXT-01 flags only a train that *runs into* the exit. A train that enters the section there, or a plan cut at its horizon with only that entry call, is not flagged.

**Line choice, sub-blocks and single-line working in CP-SAT** (issue #153):
- **Direction of traffic.** On the reversible MID line of KTV-VZM, the IR marks every block and sub-block operation with a gate and a direction. Trains of opposite directions never overlap on the line, while trains of one direction follow sub-block by sub-block.
- **Line blocks.** Closures and `line_block` incidents make their line's resources unavailable over the block. With single-line working on an absolute block, the direction that lost its line may use the other line against its direction, with the blocked line's Connections, while the working lasts.
- **Checks.** These two extensions of the DISPLIB document (`gate`, `unavailable`) are enforced by the CP-SAT model and checked by G1. A CP-SAT schedule therefore has no head-on movement and no deadlock: every train holds its resources until its next operation starts.
- **A5 and terminations.** A short-terminated or cancelled train adds its class weight times `opt.disruption.terminate_penalty_min` to A5, the penalty the planner ranks it with. Without this, terminating a late train removed the delay it would have accrued, and an arm that terminated trains looked better on A5.
- **Loop scenarios.** A line block under single-line working goes through the injector, so the world and every plan work the other line both ways, instead of a closure that only holds trains. The scenario report counts the executed head-on movements (VER-BLK-04), deadlocks and stuck trains of each arm.

**Disruption options** (issue #156, FR-OPT-11; register `opt.disruption`):
- **What the plans assume.** An incident announced with an ETR is planned to end at that ETR until it clears or the ETR passes. At that point the injector announces the real end, plans use it, and the loop re-plans on a `disruption` trigger. Both events re-open the commit window. The last plan's frozen decisions and the orders not yet executed were made without the incident (a train due to leave into a dead OHE zone cannot), so the loop withdraws them and the cycle's options give the new orders. Decisions that have started stay history.
- **Re-order / re-cross.** While an incident or closure is in force in the horizon, a `recross` option serves first the train the greedy plan holds longest. The trains it waited for wait instead, so the crossing or overtaking happens at another station.
- **Short-terminate / cancel.** This option is policy-gated (`short_terminate`). For the passenger train the plan holds longest, if the hold is at least `terminate_min_hold_min` at a station where it stops, a `short-term` option ends its run there. A `cancel` option is offered instead when that station is its first and the train has not entered. The option ranks with `terminate_penalty_min` passenger delay-minutes added. Accepted, it becomes the order `SM/<station>: short-terminate <train>` (or `Control: cancel <train>`) and the twin directive `Directives.terminations`. The world, every later plan, G3 and the day's KPIs apply it, and a terminated train does not count as having left the section (B1).
- **Contingent ETR plans.** On a disruption cycle with an incident whose ETR is still ahead, the planner builds plans for ETR + each of `contingent_etr_min` (30, 60 min). The cycle's own plan is the plan for the ETR. Each is verified (G2, G3) in its own scenario, where the incident lasts that much longer. The cycle record lists them with the switch rule ("if not restored by hh:mm, switch to the plan for ETR hh:mm").
- **First-plan latency (DSC-06).** `CycleRecord.first_plan_s` is the wall time to the first verified option. The scenario report gives its P95 over the disruption cycles.
- **Single-line working in G2 / G3.** A plan run under a loco failure with single-line working is audited against the network as it was worked (`network.as_worked`: the remaining line both ways, the blocked line's connections borrowed).
- **Evidence.** `python -m raildss_orchestrator scenarios SCN-04 SCN-08 SCN-09 SCN-10 --arm BL-1 --span-min 30 --report docs/kpi/scenarios-disruption-options.md`.
- **G3 under the incidents.** The G3 replay of a plan now applies the standing incidents, late entries and terminations, as its baseline does.

Limits: the planner forks the twin, so its state equals the DSS estimate only while nothing diverges; the
CP-SAT instance does not model OHE outages, loco failures or crew holds (G3 judges them in the twin); CP-SAT needs about
6 s to build its model, so `opt.time_limit_s.routine` (15 s) leaves it about 9 s to improve the hint.
