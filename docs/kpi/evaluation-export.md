# Evaluation export (issues #173, #168)

The evaluation runner (#168) writes one JSON document per evaluation run; the management KPI view of the web app
(#173) reads it from `web/public/data/evaluation.json`. The schema is
[`evaluation.schema.json`](evaluation.schema.json) (JSON Schema 2020-12). It is deliberately small: run metadata
and a flat list of rows.

```json
{
  "schema_version": 1,
  "run": {
    "network_version": "KTV-PSA-2025-08-r2",
    "register_version": "2026.11.1+1618c2a745ca",
    "scenario_set": "library-v1",
    "seed": 0,
    "generated_by": "python -m raildss_orchestrator evaluate ...",
    "sample": false
  },
  "rows": [
    {
      "scenario": "SCN-05",
      "arm": "BL-2",
      "kpis": { "A5_weighted_delay_train_min": 7100, "D2_resolved_before_occurrence_pct": 92 },
      "plans_verified": { "verified": 70, "built": 74 },
      "notes": ""
    }
  ]
}
```

## Producing an export (issue #168)

```
python -m raildss_orchestrator evaluate SCN-05 [SCN-xx ...] [--arm BL-0 --arm BL-2 ...] [--span-min 30] [--out-dir docs/kpi/evaluation]
```

The runner (`raildss_orchestrator.evaluate`) runs each scenario under every arm on the same day, demand,
seed and disruptions:
- **BL-0** is the loop's own BL-0 run of the day.
- **BL-1, BL-2 and BL-3** run the decision loop in the scenario's window: greedy, CP-SAT, and CP-SAT with the
  degraded predictor.
- **BL-5** is the freight log of the scenario's freight day, where the log has one.

The loop arms report the day's KPIs plus D1, D2, H1 and H2 of the window. Per scenario the runner writes
`<id>.json` (an export with that scenario's rows) and `<id>.md` (KPIs by arm, differences from BL-0 and
BL-2 − BL-1). With several scenarios it also writes `evaluation.json` with all their rows; copy that to
`web/public/data/evaluation.json` for the management view.

## Run (`run`)

| Field | Required | Meaning |
|---|---|---|
| `network_version` | yes | The published network, for example `KTV-PSA-2025-08-r2`. |
| `register_version` | yes | The parameter register version stamp (`2026.11.1+<hash>`). |
| `scenario_set` | yes | What was evaluated: `library-v1`, `nominal-days`, `real-days` ... |
| `seed` | yes | The run seed (scenario seeds are in the scenario files). |
| `date` | no | The day, for a single-day evaluation (ISO date). |
| `window` | no | `{from_h, to_h}`: the decision-loop window in hours, when one was used. |
| `generated_by`, `generated_on`, `notes` | no | The command, the date of the run and free text. |
| `sample` | no | `true` marks hand-made sample data. The view then shows a **sample-data banner**. |

## Rows (`rows`)

One row per scenario and arm; a `(scenario, arm)` pair appears once (the tests check it).

| Field | Required | Meaning |
|---|---|---|
| `scenario` | yes | A scenario id (`SCN-05`) or a day label (`day 2025-08-20`). |
| `title` | no | A readable name (given once per scenario, on any of its rows). |
| `arm` | yes | `BL-0` no action; `BL-1` greedy (controller practice); `BL-2` and `DSS` the DSS with auto-accept (`DSS` is the name the loop reports use, the view treats both alike); `BL-3` the DSS with a degraded predictor (`pred.degraded`); `BL-5` the historical reference (K1, freight only). |
| `kpis` | yes | KPI name to number, or `null` when the arm cannot measure it (BL-0 and BL-5 have no conflict log or alerts). At least one KPI. |
| `plans_verified` | no | `{verified, built}`: candidate plans that passed G0-G3 and plans built in the loop; `null` for an arm without a loop. |
| `notes` | no | Free text. |

## KPI names

The names are those the code already emits, so a producer copies them from the KPI engine
(`raildss_dss.kpi`), the conflict KPIs (`raildss_dss.conflict_kpi`, `ConflictKpis.as_dict`) and the alert KPIs
(`raildss_dss.alert_kpi`, `AlertKpis.as_dict`). A name is a letter, digits, an underscore and a name
(`^[A-Z][0-9]+_[A-Za-z0-9_]+$`); any such name is allowed, the schema documents these:

| KPI | Meaning | Target (spec §12.4) |
|---|---|---|
| `A5_weighted_delay_train_min` | Priority-weighted delay, train-min | DSC-01: DSS at least 15 % below BL-1 and 25 % below BL-0 |
| `A2_section_punctuality_pct` | Section punctuality, % | DSC-02: DSS at least 5 points above BL-1 |
| `A2_lop_count` | Trains late at exit | |
| `A4_mean_arrival_delay_min`, `A4_max_arrival_delay_min` | Arrival delay | |
| `A6_mean_exit_delay_min`, `A6_median_exit_delay_min`, `A6_p90_exit_delay_min` | Exit delay | |
| `A9_mean_time_lost_min` | Time lost against the booked time | |
| `B1_trains_exited` | Trains that left the section | |
| `C1_freight_speed_kmh`, `C2_freight_detention_h` | Freight speed, detention | DSC-03: detention not worse than 5 % against BL-1 |
| `F1_unplanned_stops` | Unplanned stops | |
| `D1_median_conflict_lead_min` | Median conflict lead time | DSC-05: at least 15 min |
| `D2_resolved_before_occurrence_pct` | Conflicts resolved before occurrence | DSC-04: at least 90 % |
| `H1_alerts_per_controller_hour` | Alerts per controller-hour | DSC-09: at most 6 |
| `H2_flood_events` | Flood events | DSC-09: none |

The view marks D1, D2, H1 and H2 against their absolute targets and shows every other KPI as a difference from
BL-0 (and from BL-1 for the DSS arms). It shows only the KPIs present in the file.

## The view

`web/src/evaluation` (lazy chunk `EvaluationView`): the **Management** button or `E` opens it. It reads
`data/evaluation.json`, shows a banner **SAMPLE DATA** while `run.sample` is true, lists the run, and shows
per scenario one column per arm with the difference from BL-0 and the DSC targets. "All scenarios" is the mean
over the scenarios that have a row for **every** loop arm of the file (BL-5 aside), so that the arms are compared
on the same scenarios; a scenario that lacks an arm is chosen on its own. BL-5 (history) is a different measure
(the freight log): its own rows, no difference from BL-0.
The committed `web/public/data/evaluation.json` is a hand-made fixture with `sample: true` until the runner
produces a real export; `tests/docs/test_evaluation_schema.py` validates it (and the schema) and keeps the KPI
names in step with the code.
