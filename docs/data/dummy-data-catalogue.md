# Dummy-data catalogue (issue #24, M0-24)

Spec v1.3: C-14 (no requests to IR, CRIS or the division), C-03 (no NTES / running-status scraping),
§6.3–6.4, Appendix B. Every input the KTV–PSA dataset lacks is filled from an **open-internet source
with recorded provenance** or by a **clearly labelled DUMMY placeholder**.

**How to read this catalogue**

- One row per "Synthetic", "Dummy" or "Mixed" row of spec §6.3 and per missing item of spec §6.4.
- *Register keys* lists the `config/parameters.yaml` entries that carry the values, with their status in brackets.
- Every value introduced here has `status: DUMMY` and a `provenance` (URL or `placeholder`). It also appears in the generated [dummy-values report](../params/dummy-values.md).
- Rows that need **no new value** point to the existing key or to the issue that owns the structural work.
- `tests/params/test_dummy_catalogue.py` checks this table against the register.
- **Checked** is the date the source was last opened or searched.
- **Confidence:**
  - **M**: a secondary source states the value.
  - **L**: an indirect source, or the value is outside its context.
  - **P**: placeholder, with no source.

## Catalogue

| ID | Missing item (spec row) | Register keys | Value / generator parameters | Source | Licence | Checked | Conf. | Owner |
|---|---|---|---|---|---|---|---|---|
| DC-01 | Signals, overlaps, station routes (§6.3 signals; §6.4 row 1) | `signal.macl.placement_m` (DUMMY) | Distant 1,000 m before home. Home ≥ 180 m (double line) / 300 m (single line) in rear of the first facing points. Overlap = `block.adequate_distance_m.macls` (180 m, V). Twin braking check uses `brake.decel_mps2` (passenger, SA): (aspects − 2) × shortest sub-block ≥ v²/2a | [railwaysignallingconcepts.in](https://www.railwaysignallingconcepts.in/railway-signals-location-home-signal-distant-signal-routing-signal-starter-signal-advanced-starter/); [IRFCA FAQ](https://irfca.org/faq/faq-signal.html) ("~1 km") | Facts cited, nothing copied | 2026-11-03 | M | #13 synthesises |
| DC-02 | Automatic-signal spacing KTV–VZM (§6.3; §6.4 row 2; OD-32) | `abs.subblock_spacing_km.ktv_vzm` (DUMMY) | 1.0 km; sensitivity 1.25 / 1.5 km | Railway Board letter of 9 Oct 2024 as reported in search results (PDF 404). Re-check: the only Oct 2024 RB letter found is electrical; secondary sources give 1–1.5 km | Facts cited | 2026-11-03 | L | #13 |
| DC-03 | Gradients and curves (§6.3; §6.4 row 3; OD-30) | `gradient.dem_source` (DUMMY) | Copernicus DEM GLO-30 (30 m), fallback SRTM 1″. Curves from OSM geometry, cross-checked against the real PSR reasons in `psr.list` (DATA) | [Copernicus GLO-30 licence](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf); OSM | GLO-30: free, attribution required; OSM: ODbL (decision in #23) | 2026-11-03 | M | #14 |
| DC-04 | TSRs / caution orders (§6.3; §6.4 row 4) | `tsr.generator` (DUMMY) | 0.5 new per day, 3 active on average, length 200–3,000 m, speeds 20/30/45/60/75 km/h, validity 1–180 days | [IRFCA caution orders](https://irfca.org/docs/caution-orders.html); GR 4.09 / 15.09 via [IRFCA rulebook](https://irfca.org/docs/rulebook/gr-general.html). 6-month cap: study source, unverified | Facts cited | 2026-11-03 | P (rates) / M (practice) | M1 generator |
| DC-05 | Rolling-stock dynamics (§6.3 Mixed; §6.4 row 5) | `loco.WAP7` (V), `loco.WAG9` (V), `loco.WAG12B` (U), `loco.length_m` (DUMMY), `loco.te_curve` (SA), `resistance.davis` (SA), `brake.decel_mps2` (SA), `consist.passenger.coach` (DUMMY), `consist.passenger.loco_class` (SA) | Loco lengths WAP-7 / WAG-9 20.562 m, WAG-12B 35.0 m. TE(v) = min(TE_start, P_rail / v) every 5 km/h, P_rail from the continuous rating or 0.9 × rated power. Davis r = a + b·v + c·v² (N/t): loco 15 / 0.1 / 0.003, passenger 9 / 0.04 / 0.0011, freight loaded 8 / 0.02 / 0.0012, empty 10 / 0.03 / 0.0035. Braking 0.5 (passenger, light engine) / 0.25 (freight loaded) / 0.3 (empty) m/s². LHB coach 42 t tare, 50 t gross, 160 km/h. Passenger loco WAP-7 | Loco values: R05 §3.2 and [Wikipedia WAP-7](https://en.wikipedia.org/wiki/Indian_locomotive_class_WAP-7) infoboxes (secondary). Davis, braking and coach mass: placeholder, calibrated in #16 | CC BY-SA facts | 2026-11-04 | M (locos) / P (dynamics) | #15 |
| DC-06 | Freight consist length, loco class (§6.3) | `consist.freight.rake` (V), `consist.freight.wagon_family` (DUMMY), `consist.freight.wagon_type_family` (SA), `consist.freight.loco_class` (SA) | 13 wagon families with rake size, length over couplers, tare, payload and loaded/empty vmax. 58 BOXN ≈ 622 m + loco (V). All 47 wagon codes in the Aug 2025 freight log map to a family. WAG-9 by default, WAG-12B for loaded rakes > 4,500 t | BOXN, BCN and BRN dimensions from [IRIMEE wagon notes](https://rskr.irimee.in/sites/default/files/Types%20of%20Wagons%20%2C%20nomenclature%20and%20their%20design%20features.pdf) as reported by web search; BOXNHL rake of 60 incl. 2 spares from [ECoR CC-67](https://eastcoastrail.indianrailways.gov.in/uploads/files/1741610499161-CC%2067.pdf). Other rake sizes, masses and speeds: placeholder | Government publication, facts cited | 2026-11-04 | M (BOXN/BCN/BRN) / P (rest) | #15 |
| DC-07 | Passenger actual running and delays (§6.3; §6.4 row 6) | `delay.passenger.entry_profile` (DUMMY), `delay.noise` (DUMMY) | Entry-delay mixture: on-time mass P(on) premium 0.65 / M/E 0.55 / passenger 0.45, uniform −2…+5 min; log-normal tail μ = 3.0 (ln min), σ = 1.0. Implied ≤ 15 min share 0.79 / 0.72 / 0.66, inside the 0.65–0.75 ECoR-adjusted band for M/E. Dwell noise σ 0.3; run-time SD 3 % / 5 % | National M/E punctuality 77.12 % FY 2024-25 ([Lok Sabha via Devdiscourse](https://www.devdiscourse.com/article/business/3718465-overall-punctuality-of-mail-express-trains-was-80-in-2025-26-vaishnaw)); ECoR 64.4 % vs 73.91 % in one week of May 2023 (Wikipedia, secondary). Noise: placeholder | Facts cited | 2026-11-03 | L | M1 |
| DC-08 | Disruptions (§6.3; §6.4 row 7) | `disruption.generator` (DUMMY) | Per section-day: signal failure 0.6, point failure 0.3, OHE trip 0.15, loco failure 0.2, hot axle 0.2, ACP/cattle/LC 3.0, rail fracture 0.05, crew shortage 0.1; durations per register | placeholder (mid-points of R05 §7.4) | — | 2026-11-03 | P | M1–M3 |
| DC-09 | Crew (§6.3; §6.4 row 8) | `crew.duty_limit_h` (DUMMY), `crew.sign_on_lead_min` (DUMMY) | Running 9 h, sign-on to sign-off 11 h, crew change at PSA (DATA). Sign-on 30 min before departure | [Zee Business](https://www.zeebiz.com/india/news-indian-railways-union-demands-shorter-duty-hours-more-rest-for-loco-pilots-60273/amp) (secondary); sign-on: placeholder | Facts cited | 2026-11-03 | M / P | M1 |
| DC-10 | Freight demand variety (§6.4 row 9) | `freight.arrival_rate_per_h.ktv` (DATA), `freight.trains_per_day` (DATA) | No new value: NHPP fitted to the 27 real days | `WAT_GOODS_TRAIN_AUG25` | Dataset (confirmed free to use) | 2026-11-03 | — | #11 |
| DC-11 | Positions, RTIS-like observation model (§6.3; §6.4 degradation profiles) | `rtis.position_interval_s` (V), `obs.station_event_resolution_s` (DATA), `obs.degradation_profile` (DUMMY) | D0 clean; D1 from DATA keys; D2: RTIS 50 %, dropout 5 %, latency 0–5 min, missing departures 2 %, duplicates 2 %, out-of-order 1 %, 10 min outage every 4 h; D3: D1 + 15–30 min outage | Spec §6.4 table; placeholder for D2/D3 | — | 2026-11-03 | P | #19 / M1 |
| DC-12 | Rules: station working rules (§6.3 Mixed) | `swr.template` (DUMMY) | No simultaneous reception, no run-through on loops, no isolation assumed (conservative) | placeholder. Lead: ECoR SWR for Srikakulam Road (CHE) indexed at [eastcoastrail …/1616135639700-CHE.pdf](https://eastcoastrail.indianrailways.gov.in/uploads/files/1616135639700-CHE.pdf), HTTP 404 on 2026-11-03 | Government publication | 2026-11-03 | P | #13 / M1 verifier |
| DC-13 | Palasa CSR (§6.3; OD-31) | `station_line.csr.psa` (EST) | No new value. Central 733 / 752 m, bounds 673–1,150 m. The OSM yard re-run (Overpass, 2026-11-03) again shows 9 non-main ways of ≈ 0.8–1.3 km; the per-station calibration remains open | R06 App. C; OSM | ODbL | 2026-11-03 | L | #8 |
| DC-14 | Face validity (§6.4 last row) | — | Decision record DR-24-1 below | — | — | 2026-11-03 | — | M1–M3 |

## Open-data search pass (issue #24 scope, 2026-11-03)

| Target | Result | Decision |
|---|---|---|
| Open, licence-clean dataset of IR passenger running | **None found.** Published material is aggregates only: national M/E punctuality (Lok Sabha replies, PIB), a dated 2017-18 zone-wise Lok Sabha table, and Dataful's national yearly index (licence not stated). Kaggle and research sets are scraped from running-status sites, so they are excluded by C-03 | Keep DC-07 as DUMMY, calibrated to aggregates |
| Public L-sections / gradients, Vizianagaram–Palasa | **None found.** ECoR station working rules (which list yard gradients) exist for some Waltair stations; the CHE copy is no longer served | DEM method (DC-03). Recover the CHE SWR if a copy appears |
| Caution-order practice documents | IRFCA caution-order article and GR 4.09 / 15.09 pages. Validity cap of 6 months only in study material | DC-04 |
| IRSEM para 7.1 text | Primary text **not found online**. Secondary sources give distant ≈ 1 km (≥ braking distance) and home ≥ 180 m / 300 m before facing points | DC-01 |
| ABS signal spacing letter (OD-32) | The 9 Oct 2024 PDF is still 404. A CRS abstract cites an RB letter of 8 Oct 2024 (2000/Elect(G)/113/4/Safety), which is electrical. Secondary sources: 1–1.5 km | DC-02 unchanged |
| Context change | Waltair division became **Visakhapatnam division** under the new South Coast Railway zone (operational 1 Jun 2026). Palasa–Ichchapuram moved in from Khurda Road | The dataset keeps its 2025 codes (WAT / KUR / ECoR). No ETL change |

**Downloaded files:** none were added to the repository by this issue. DEM and OSM extracts are fetched and cached by #14 under the licences above. The licence gate decision belongs to #23.

**Excluded sources (C-03):** NTES and every running-status site or API (runningstatus.in, etrain.info, erail, railyatri, confirmtkt, trainspnrstatus and similar), plus any dataset scraped from them. The test fails if any of these appear as a provenance.

## Decision record DR-24-1: reviewer arrangement

- **Decision:** **public sources only.** No informal SME (e.g. a retired controller) is available without departmental involvement. Rule fidelity is therefore stated as **"per public sources"** (spec A-05, RK-17).
- **Consequence:** the face-validity reviews in spec §6.4 remain open until a reviewer can be engaged without departmental involvement. This decision must be recorded in the M0 exit review (#25).
- **Date:** 2026-11-03. **Status:** accepted for M0.
