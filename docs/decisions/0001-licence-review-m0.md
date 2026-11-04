# DR-0001: M0 licence review: train-graph component, Grafana, DEM/OSM, DISPLIB

| Field | Value |
|---|---|
| Issue | #23 (M0-23) |
| Spec references | REC-19, REC-10, C-07, NFR-LIC-01…06, FR-DATA-05, OD-16, OD-17, A-07, RK-07 |
| Date | 2026-11-03 |
| Status | **Proposed.** The project owner decides in the PR review. **Merging the PR accepts the recommendations below.** Requesting changes on §2 selects the alternative posture or the fallback chart |
| Supersedes | n/a |

> This is an engineering licence review, not legal advice. Every licence fact cited below was
> checked against the primary source on 2026-11-02 or 2026-11-03 (links inline). If RailDSS is ever
> handed to a third party (for example a railway administration), repeat the review with that party's
> legal team.

## Decision summary

| # | Question | Decision (recommended) | Alternative / fallback |
|---|---|---|---|
| D1 | Deployment and licensing posture (OD-16) | **Public open source under Apache-2.0** (the `LICENSE` on `main` since PR #27). Data keeps its own licences, recorded per provenance card | Closed demo (private repository, no distribution) |
| D2 | Train-graph component (REC-19, NFR-LIC-03) | **Chart component: OSRD `@osrd-project/ui-charts` SpaceTimeChart (LGPL-3.0-or-later), unmodified, pinned, shipped as a separately loaded chunk**, under the conditions in §3.3 | **In-house D3 + Canvas** component (ISC / Apache-2.0), triggered by any condition in §3.4 |
| D3 | Grafana (NFR-LIC-04, REC-10) | **Confirmed: unmodified Grafana OSS (`grafana/grafana` image, AGPL-3.0), run as a separate service**, never forked, embedded as code or patched | Apache Superset (Apache-2.0) |
| D4 | DEM and OSM attribution (FR-DATA-05) | **Copernicus DEM GLO-30** is the primary DEM. SRTM GL1 v003 is the fallback. OSM data is under ODbL 1.0. The attribution texts in §5 go into every provenance card, the root `NOTICE` and the map UI | — |
| D5 | DISPLIB licence (OD-17, A-07) | **Licence still unstated.** Internal use only: no DISPLIB file is committed to this public repository. The request to SINTEF is **drafted, awaiting the user to send** ([request](../licensing/displib-licence-request.md)) | INFORMS RAS 2012 instances as the external benchmark |

## 1. Context

- RailDSS code is licensed **Apache-2.0** (`LICENSE`, `pyproject.toml` `license = "Apache-2.0"`). The repository `Gous0786/RailDSS` is **public**, so everything committed is distributed.
- C-07 / NFR-LIC-02 forbid BSL, ELv2, SSPL, Prosperity and linked GPL/AGPL. They allow LGPL (the OSRD chart) and AGPL (Grafana) **only after this review**, and only as separate or unmodified components. `tools/check_licences.py` enforces this for Python dependencies. It reports LGPL as "review" and unknown licences as warnings.
- REC-19 names the OSRD SpaceTimeChart as the primary train graph "after the M0 licence review (OD-16)". The fallback is an in-house D3 + Canvas component. The GPL Marey gist is a reference only.
- REC-10 limits Grafana to system health, analyst time series and evaluation runs. Controller and management views are built in the RailDSS web app.

## 2. OD-16: deployment and licensing posture

### 2.1 Options

| Option | Description | Effect on LGPL chart | Effect on AGPL Grafana | Effect on data licences |
|---|---|---|---|---|
| **A. Public open source, Apache-2.0** (status quo) | Code public under Apache-2.0. Anyone may run, fork or redeploy RailDSS. Hosted demos serve the web UI to browsers | **Lowest risk.** The full source of the combined work (our app + build scripts) is public, so LGPL-3.0 §4(d)(0) (recombine/relink) is met. The remaining duties are notices and pointing to the library source (§3.3) | Unmodified use as a service has no source-release duty (AGPL §13 applies only to modified versions) | Public repo = distribution. ODbL share-alike applies to any committed OSM-derived database. Copernicus notices and disclaimer must ship with any committed DEM-derived data. DISPLIB files must not be committed |
| B. Closed demo | Private repository. Runs only on project machines. Browsers still receive the JS bundle | Acceptable. Serving the bundle to users is still "conveying" the library, so the §3.3 duties remain. Relinking is offered via the separate chunk (§4(d)(1)) | Same as A | Fewer distribution events, but a hosted demo is still "communication to the public" for Copernicus and a public use of an ODbL database |
| C. Apache-2.0 code delivered to an external operator as a hosted service or binary | As A, plus a hand-over | Acceptable under A's conditions. The operator must receive the `NOTICE` and LGPL texts | Operator must not modify Grafana without publishing changes | Operator inherits the Copernicus (Art. 6(e)) and ODbL obligations through `NOTICE` |

### 2.2 Recommendation

**Adopt option A.** It is already true (Apache-2.0 `LICENSE` on a public repository), it matches NFR-LIC-01, and it makes the LGPL question almost trivial: the "recombine with a modified library" right that LGPL protects is automatically available when the whole application source is public.

**OD-16 proposed resolution:** *Closed: public open source under Apache-2.0. Third-party components keep their own licences (LGPL chart unmodified and separately loaded; Grafana unmodified as a separate service). Data keeps its own licences per provenance card.*
The spec owner should update §15 OD-16 after this PR is merged; this PR does not edit the spec.

## 3. D2: train-graph component

### 3.1 Facts (verified on 2026-11-02/03)

| Item | Value | Source |
|---|---|---|
| Package | `@osrd-project/ui-charts` 0.0.84 (published 2026-09-07). 14 versions since 0.0.68 (2025-03-13) | npm registry `https://registry.npmjs.org/@osrd-project/ui-charts` |
| Licence | `LGPL-3.0-or-later` (npm metadata and `front/ui/ui-charts/package.json` in the OSRD monorepo). OSRD as a whole: LGPL-3.0-only (`publiccode.yml`) | https://github.com/OpenRailAssociation/osrd |
| Source | `front/ui/ui-charts/src/spaceTimeChart/` in `OpenRailAssociation/osrd`. The former `osrd-ui` repository is archived | https://github.com/OpenRailAssociation/osrd/tree/dev/front/ui/ui-charts |
| Runtime dependencies | `@osrd-project/ui-core` 0.0.84 (LGPL-3.0-or-later), `@osrd-project/ui-icons` 0.0.84 (LGPL-3.0-or-later), `chroma-js` 3.2.0 (BSD-3-Clause AND Apache-2.0), `d3-selection` / `d3-zoom` 3.0.x (ISC), `lodash` 4.18.1 (MIT) | npm registry |
| Peer dependencies | `react` / `react-dom` ^18 or ^19.1, `classnames` ≥2.5 (MIT), **`tailwindcss` ≥4.1** (MIT) | npm registry |
| Design | React component. Canvas rendering with a picking layer for interaction. **Stateless**: paths, scales, offsets and zoom are owned by the parent | `spaceTimeChart/README.md` |

### 3.2 Analysis

- **Compatibility:** Apache-2.0 application code may use an LGPL-3.0 library. Apache-2.0 is compatible with (L)GPL-3.0, and LGPL-3.0 §4 permits combined works under any terms that allow customer modification and debugging reverse engineering. Apache-2.0 allows both.
- **Is a web app "conveying"?** Yes, conservatively. The JS bundle is sent to every user's browser. So the LGPL-3.0 §4 "Combined Works" duties apply, both on any hosted demo and when the repo is cloned.
- **Duties under LGPL-3.0 §4 for an unmodified library:**
  - (a) say the library is used and is under LGPL;
  - (b) ship the GPL-3.0 and LGPL-3.0 texts;
  - (c) include the library's copyright notice where the app displays copyright notices (About page);
  - (d) allow relinking: either (0) provide the application's Corresponding Application Code (met by the public source) or (1) use a mechanism that loads the library separately at run time;
  - (e) Installation Information (only for "User Products", so not applicable to a web app).

  Library source must be available (GPL-3.0 §6(d) via LGPL §4(d)(0)): linking to the exact OSRD release tag is enough while we maintain the link.
- **Modification risk:** If we ever need to change the component, the change must be published under LGPL. Under option A this is free, because we publish everything anyway, but the fork becomes a maintenance burden.
- **Engineering risk (not licence):** The package is pre-1.0 (0.0.x), so the API can change between releases. The `tailwindcss` ≥4.1 peer dependency constrains our frontend styling stack.

### 3.3 Conditions of use (all mandatory)

1. **Unmodified.** Use the published npm packages `@osrd-project/ui-charts`, `ui-core` and `ui-icons` as-is. Pin exact versions in `package.json` and the lockfile; no `^` ranges. Any patch goes upstream to OSRD first. A local fork is allowed only if it is published under LGPL-3.0-or-later in a public repository, recorded in a new decision record.
2. **Separate chunk.** The bundler config emits the `@osrd-project/*` packages into their own chunk (for example Vite `build.rollupOptions.output.manualChunks`), loaded at run time and not inlined into application chunks. Keep the chunk unminified-name-mapped or ship its source map, so it stays replaceable.
3. **Notices.**
   - The web app's About / Licences page lists `@osrd-project/ui-charts`, `ui-core` and `ui-icons` with "LGPL-3.0-or-later, © The OSRD Contributors", plus links to the LGPL-3.0 and GPL-3.0 texts and to the exact OSRD source tag.
   - The root `NOTICE` carries the same entry once the frontend lands.
4. **Licence gate for npm.** When the frontend is added (M1), CI gains an npm licence check equivalent to `tools/check_licences.py`. It **allow-lists exactly these three LGPL packages** with a reference to this record, and forbids every other LGPL, GPL, AGPL, BSL, ELv2, SSPL or Prosperity package.
5. **No GPL reference code.** The Marey gist (GPL) stays a reference only (REC-19): no copied code.

### 3.4 Fallback triggers: switch to the in-house D3 + Canvas component if any of these occur

- The owner chooses a posture in which condition 2 or 3 cannot be met. An example is a vendor requirement for a single, sealed, minified bundle.
- We need a functional change that OSRD will not accept upstream and we are not willing to maintain a public LGPL fork.
- An upstream release changes the licence, or adds a dependency under a forbidden licence.
- The 0.0.x API churn costs more than the ~2–3 weeks the component saves (REC-19). Re-evaluate at the end of the first M1 UI sprint.

The fallback uses `d3-scale`, `d3-zoom` and `d3-selection` (ISC) on HTML Canvas, written in-house under Apache-2.0. ECharts custom series (Apache-2.0) is the second fallback.

## 4. D3: Grafana

**Confirmed: Grafana is used unmodified, as a separate service** (NFR-LIC-04, REC-10). The rules:

1. **Image.** Use the open-source edition image `grafana/grafana:<pinned version>` (AGPL-3.0). Since Grafana 12.4.0, `grafana/grafana-oss` is no longer updated, and the v13.1 docs list `grafana/grafana` as the open-source image and `grafana/grafana-enterprise` as the Enterprise image ([Grafana Docker docs](https://grafana.com/docs/grafana/v13.1/setup-grafana/installation/docker/)). **Do not use `grafana/grafana-enterprise`.** It is distributed under Grafana Labs' own licence terms, so it would need its own review.
2. **Unmodified.** No fork, patch, rebuild or custom build of Grafana. Configuration (`provisioning/` YAML, dashboard JSON, data-source definitions) is our own Apache-2.0 content. It is input to Grafana, not a modification of it.
3. **Separate service.** Grafana runs in its own container and talks to Postgres/TimescaleDB and Prometheus over the network. The RailDSS web app links to Grafana or embeds it via `<iframe>` only. It never imports Grafana source or packages (`@grafana/*` npm packages included).
4. **Plugins.** Use only bundled plugins or plugins under an OSI-approved licence, reviewed in the PR that adds them. Do not write custom Grafana plugins without a new decision record.
5. **Scope.** Grafana is limited to system health, analyst time series and evaluation runs (REC-10). Controller and management KPI views live in the RailDSS app.
6. **Compose / CI.** The PR that adds the Grafana service to `docker-compose.yml` (observability, PL-4) must pin the image tag and reference this record. Container images are not covered by `pip-licenses`, so that PR's reviewer checks the image against rule 1.

Under these rules AGPL-3.0 §13 (network interaction with a *modified* program) never triggers. No RailDSS code becomes subject to AGPL.

## 5. D4: DEM and OSM attribution obligations

### 5.1 Copernicus DEM GLO-30 (primary DEM)

Licence: *Licence for Copernicus DEM instance COP-DEM-GLO-30-F Global 30m Full, Free & Open* ([PDF](https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf), read 2026-11-03). Art. 4 grants reproduction, distribution, communication to the public, and adaptation, modification and combination, free of charge (Art. 5), worldwide.

Obligations (Art. 6):

| Art. | Obligation | RailDSS action |
|---|---|---|
| 6(a) | Unmodified data distributed or shown publicly: notice **"© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved."** | Only if raw DEM tiles are ever committed or served. The M0-14 plan caches the tiles outside `data/raw` |
| 6(b) | Adapted or modified data (our gradient profiles): notice **"produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved"** | In the provenance card of every DEM-derived dataset, in `NOTICE`, and in any UI or report that shows gradient profiles |
| 6(c) | Liability disclaimer in the licence or legal notice covering the distribution: **"The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30."** | In `NOTICE` and the provenance card |
| 6(d) | Do not imply endorsement by the Provider, the Licensor or Copernicus bodies | No Copernicus or ESA logos. No "endorsed by" wording |
| 6(e) | Pass these obligations on to subsequent users | The Apache-2.0 `NOTICE` mechanism (§4(d)) requires redistributors to keep `NOTICE`. The provenance card repeats the obligations |

GLO-30 is free for all countries except Armenia and Azerbaijan, so India is covered.

### 5.2 SRTM GL1 v003 (fallback DEM)

SRTM is a NASA/NGA product distributed openly by USGS EROS and NASA LP DAAC. I did not find a single explicit licence statement in this review. US-government works are generally not under US copyright, but **check the product page before relying on that**. If SRTM is used, cite it as: *NASA JPL (2013). NASA Shuttle Radar Topography Mission Global 1 arc second [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/MEaSUREs/SRTM/SRTMGL1.003*.

### 5.3 OpenStreetMap (ODbL 1.0)

Per the OSMF Attribution Guidelines ([osmfoundation.org](https://osmfoundation.org/wiki/Licence/Attribution_Guidelines)):

- **Text:** **"© OpenStreetMap contributors"**, linked to https://www.openstreetmap.org/copyright. The licence is the ODbL 1.0, https://opendatacommons.org/licenses/odbl/1-0/.
- **Interactive map (RailDSS UI):** attribution in a map corner (the MapLibre attribution control) that stays findable (FR-DATA-05).
- **Datasets and derived databases:** attribution plus the ODbL text or a link to it, in a README or metadata file next to the data. This means the provenance card.
- **Printed or PDF reports:** credit beside the map or in the acknowledgements, with the URL `openstreetmap.org/copyright` printed in full.
- **Share-alike:** this repository is public, so committing a **derivative database** built from OSM is a public use. That database must be offered under ODbL 1.0, as must a gradient or curve table whose geometry or positions come from OSM. Only that database is affected, not RailDSS code, so **keep OSM-derived data in its own files and mark them ODbL in the provenance card**. One-off extracts of **fewer than 100 features** are not "substantial" under the OSMF Substantial guideline (endorsed 2014-06-06). The Palasa yard-length high bound in `data/derived/ktv-psa/` uses a handful of ways and falls under that threshold. It is still attributed.

### 5.4 Where the attribution text lives

| Place | Content |
|---|---|
| `data/derived/ktv-psa/PROVENANCE.md` | OSM attribution (high-bound yard lengths) |
| `data/external/osm-dem/PROVENANCE.md` | Card for the M0-14 gap-fill inputs: OSM extract and Copernicus GLO-30 (SRTM fallback), with all notices. Retrieval URLs, dates and hashes are completed by M0-14 |
| `NOTICE` (repository root) | All third-party data notices, including the Copernicus disclaimer |
| RailDSS web app (M1) | Map-corner OSM attribution. About page with OSM, Copernicus and LGPL entries |
| `tests/docs/test_licence_review.py` | CI guard: any provenance card that mentions OpenStreetMap, Copernicus or SRTM must carry the matching attribution text |

## 6. D5: DISPLIB (OD-17)

**Facts (2026-11-03):**
- The site https://displib.github.io states no licence for the instances, solutions or verifier.
- The format specification `displib_spec_2025-09-17.pdf` contains no licence statement.
- The verifier archive `displib_verify_v0_3.zip` (`displib_verify.py`, `example_problem.json`, `example_solution.json`) has no licence, copyright or notice text.
- The GitHub repository `displib/displib.github.io` has no licence file.
- The paper is arXiv:2509.12254 (Kloster, Luteberget, Mannino, Sartor).

**Consequences while the licence is unknown** (default copyright, all rights reserved):
- Do **not** commit DISPLIB instances, solutions or the verifier to this public repository, and do not vendor or modify the verifier.
- Do not use `.gitignore` to hide them in the repo either (`.gitignore` is reserved for secrets). Benchmarks download them at run time to a cache **outside** the repository, record the URL and SHA-256, and stay internal (A-07).
- Implementing a DISPLIB-*compatible* intermediate representation from the published description (FR-OPT-01) is our own code and is not affected.
- Publishing aggregate benchmark results with a citation of the paper is normal academic practice. Still, ask (it is in the request) before publishing per-instance results.

**Status of the SINTEF request:** **drafted, awaiting the user to send.** Nothing has been sent. The draft and the status log are in [`docs/licensing/displib-licence-request.md`](../licensing/displib-licence-request.md). This issue may close while the request is pending (M0-23 acceptance criterion 3, as amended by the user's no-external-contact rule).

## 7. Consequences and follow-ups

- **M0-25 exit:** the chart decision is recorded here as D2 (conditional OSRD SpaceTimeChart, D3 + Canvas fallback).
- **M0-14 (gradients and curves):** fill in the retrieval URL, date and SHA-256 rows of `data/external/osm-dem/PROVENANCE.md`. Keep OSM-derived curve and gradient tables in separate files, marked ODbL. Keep DEM tiles out of `data/raw`.
- **M1 frontend:** implement §3.3 conditions 1–4 (pinned versions, separate chunk, About page, npm licence gate with the three-package allow-list) and the OSM map attribution.
- **Observability (PL-4):** add Grafana per §4.
- **Spec owner:** update OD-16 (proposed: closed, option A) and OD-17 (request drafted, awaiting send) in spec §15 after merge.
- **Re-review triggers:** a change of posture (option B or C), a hand-over to an external operator, any new LGPL/AGPL component, or a DISPLIB reply.
