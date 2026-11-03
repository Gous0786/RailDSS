# Provenance card: Palasa (PSA) station lines, derived

| Field (spec FR-DATA-03) | Value |
|---|---|
| File | `psa_station_lines_derived.csv` (10 rows: PSA lines 1–10) |
| Source | RailDSS research report 06, Appendix C ("Palasa (PSA) station lines (derived)") |
| Real / synthetic | **Derived estimate.** Line identities and directions are corrected from the raw COA tables. **CSR values are estimates** (`status = EST` in the parameter register; spec REC-23, OD-31) |
| Method | Line identity: the PSA rows of `STTNLINE` have number and name swapped; `MANSEQNUMB` = `MAVLINENAME` = Connections line number. Direction: from `Connections`. Central CSR: section median per category (loops 733 m, mains 752 m). Low bound: section p10 loop CSR (673 m) / minimum main CSR (678 m), and platform length + 57 m for platform lines. High bound: OSM yard way lengths (789–1,258 m) minus ~100 m |
| Confidence | Low–medium. Use the central values; run a sensitivity case at the low bound (spec SCN-24) |
| Seed / generator version | n/a (deterministic derivation, report 06) |
| Integrity | `MANIFEST.sha256`, verified in CI |
| Raw inputs | `data/raw/ktv-psa/KTV-PSA-Infra/STTNLINE-Table 1.csv`, `Connections-Table 1.csv`, `Platform-Table 1.csv`; OpenStreetMap (ODbL) for the high bound |
