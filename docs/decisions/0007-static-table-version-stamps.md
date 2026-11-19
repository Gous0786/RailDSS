# DR-0007: Version stamps of static tables (FR-DATA-04, DR-0002 F08)

| Field | Value |
|---|---|
| Spec references | FR-DATA-04; DR-0002 F08; DR-0006 |
| Date | 2026-11-19 |
| Status | **Accepted** (PR merged) |
| Supersedes | n/a |

## Context

FR-DATA-04 reads: static data shall be versioned (`network_version`, `timetable_version`,
`rule_catalogue_version`, `policy_version`), and every event, plan, recommendation and KPI shall be stamped with
the versions in force. Validator rule N-05 checked `network_version` on every published record and
`timetable_version` on the timetable tables only. The `policy_version` and `rule_catalogue_version` columns exist
on every entity but the publication leaves them empty, and the requirement did not say whether that is intended
(DR-0002 F08).

## Options

1. **Stamp them.** Write the register's policy and rule-catalogue versions on every published record. This changes
   every published file and the network hash, and ties the static network to versions of things it does not
   depend on: a policy-weight change would then force a new network version (DR-0006).
2. **Exempt static tables.** The network and timetable tables depend only on the network and timetable versions.
   `policy_version` and `rule_catalogue_version` are stamped where a decision is made, which is the second half of
   FR-DATA-04: events, plans, recommendations and KPIs (the KPI report already carries `policy_version`).

## Decision

Option 2. A published static table carries `network_version`, plus `timetable_version` for the timetable
tables (`service`, `timetable_point`, `freight_load`, `day_instance`, `train_run`). It **leaves
`policy_version` and `rule_catalogue_version` empty**: the two columns stay in the schema because entities
that record a decision (for example a scenario run or an event) use them.

Validator rule **N-05** enforces this: it fails if a record has a wrong or missing `network_version`, a wrong or
missing `timetable_version` on a timetable table, or a `policy_version` or `rule_catalogue_version` on a static
table. Nothing in the published data changes, so the network hash and the files are unchanged.

## Consequences

- The published tables are valid under every policy and rule-catalogue version; those versions are read from the
  parameter register and the rule catalogue at the time of a run and stamped on its outputs.
- A table that later needs a policy or catalogue stamp (a rule-bearing static table, for example) needs a new
  record that supersedes this one, and its stamps are added to N-05 at the same time.
- FR-DATA-04's wording should say "static data are versioned by `network_version` and `timetable_version`; the
  policy and rule-catalogue versions are stamped on events, plans, recommendations and KPIs". The specification
  is kept outside the repository: this record is the project's statement until the next spec revision.
