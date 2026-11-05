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
| Licence | Derived from the KTV–PSA dataset (see its card) and a one-off extract of a few OpenStreetMap yard ways. That extract is fewer than 100 features, so it is not "substantial" under the OSMF Substantial guideline (DR-0001 §5.3). |
| Attribution | Contains information from OpenStreetMap: © OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0 (https://opendatacommons.org/licenses/odbl/1-0/); see https://www.openstreetmap.org/copyright |

# Provenance card: synthetic alignment, gradients and curves (issue #14)

| Field (spec FR-DATA-03) | Value |
|---|---|
| Files | `ktv_psa_alignment_derived.csv` (route resampled every 25 m, with section km and `km_ref_hwh`), `ktv_psa_gradients_derived.csv` (`Gradient` rows, 2 km segments per block), `ktv_psa_curves_derived.csv` (`Curve` rows per block), `ktv_psa_gradients_report.json` (anchors, clipping, PSR cross-check) |
| Real / synthetic | **Synthetic** (`source = synthetic`). The dataset has no gradient or curvature data, and the national CRIS curvature table is confidential (OD-30, C-14) |
| Method | OSM rail ways form a graph; each station is anchored on its OSM station or halt node and the route is the shortest path between consecutive anchors. GLO-30 elevation is sampled along it and a running median (1 km) removes trees, bridges and cuttings. The slope over each segment is the gradient (positive = rising towards larger chainage, the DN direction), clipped at the plausibility bound (10 permille, 2 segments clipped, counted in the report). Curves: radius from the heading change across +-100 m; a run with R <= 1750 m (1 degree or more) and at least 100 m long is a curve; degree = 1750 / R. Parameters: `gradient.derivation` (SA) |
| Cross-check | PSRs whose reason is a curve are matched to detected curves by `km_ref_hwh`; the "3 degree curve" PSRs at km 810-811 are confirmed within 1 km. The PSR at KPL-KTV names points and crossings in a yard and is reported separately |
| Confidence | Low-medium. Gradients are DEM-derived and smoothed; curves depend on OSM geometry quality. Treat as plausible, not surveyed |
| Seed / generator version | n/a (deterministic from the hashed inputs in `data/external/osm-dem/inputs.yaml`) |
| Integrity | `MANIFEST.sha256`, verified in CI |
| Licence | The alignment and curve files are a derivative database of OpenStreetMap and are offered under the ODbL 1.0. The gradient file is adapted Copernicus DEM data and also uses OSM positions |
| Attribution | © OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0 (https://www.openstreetmap.org/copyright). The gradient profiles were produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved. The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30. |
