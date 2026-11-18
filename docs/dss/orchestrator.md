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

Limits: the planner forks the twin, so its state equals the DSS estimate only while nothing diverges; the
CP-SAT instance does not model closures (a plan that uses a closed line fails G3); CP-SAT needs about
6 s to build its model, so `opt.time_limit_s.routine` (15 s) leaves it about 9 s to improve the hint.
