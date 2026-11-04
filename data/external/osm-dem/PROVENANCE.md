# Provenance card: OSM and DEM gap-fill inputs (gradients and curves)

This card covers the public data that issue #14 (M0-14) uses to synthesise gradients and curves
along KTV–PSA (spec FR-TWIN-05, D-01, OD-30). Licence and attribution rows come from the licence review
[DR-0001 §5](../../../docs/decisions/0001-licence-review-m0.md#5-d4-dem-and-osm-attribution-obligations).
The retrieval rows are **to be completed by #14** when the inputs are fetched. Downloaded tiles and
extracts are cached outside `data/raw`. Only derived outputs are committed.

| Field (spec FR-DATA-03) | OpenStreetMap extract | Copernicus DEM GLO-30 (primary) | SRTM GL1 v003 (fallback, only if used) |
|---|---|---|---|
| Use | Rail geometry and positions along the route; curve estimation | Elevation sampled along the route; gradient profiles | As GLO-30 |
| Licence | Open Database License (ODbL) 1.0, https://opendatacommons.org/licenses/odbl/1-0/ | Licence for Copernicus DEM instance COP-DEM-GLO-30-F Global 30m Full, Free & Open (https://documentation.dataspace.copernicus.eu/APIs/SentinelHub/Data/DEM/resources/license/License-COPDEM-30.pdf) | NASA/USGS open distribution; no explicit licence statement found (DR-0001 §5.2) |
| Real / synthetic | Real source data; outputs are **synthetic** (`source = synthetic`) | Real source data; outputs are **synthetic** | — |
| Retrieval URL | *to be completed by #14* | *to be completed by #14* | — |
| Retrieved (date) | *to be completed by #14* | *to be completed by #14* | — |
| SHA-256 of inputs | *to be completed by #14* | *to be completed by #14* | — |
| Derived outputs | Curve tables are a **derivative database of OSM** and are offered under the ODbL 1.0. Keep them in their own files | Gradient profiles are adapted Copernicus data (Art. 6(b) notice applies). Where positions come from OSM, ODbL also applies | — |

## Attribution (copy verbatim wherever these data or their derivatives are distributed or shown)

**OpenStreetMap:** © OpenStreetMap contributors, available under the Open Database License (ODbL) 1.0;
see https://www.openstreetmap.org/copyright

**Copernicus DEM GLO-30**, unmodified tiles (Art. 6(a)):
© DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved.

**Copernicus DEM GLO-30**, adapted or modified data such as gradient profiles (Art. 6(b)):
produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA; all rights reserved

**Copernicus disclaimer** (Art. 6(c), required in any notice covering distribution):
The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability for any use of the Copernicus WorldDEM-30.

**SRTM** (only if used): NASA JPL (2013). NASA Shuttle Radar Topography Mission Global 1 arc second [Data set]. NASA EOSDIS Land Processes DAAC. https://doi.org/10.5067/MEaSUREs/SRTM/SRTMGL1.003

Do not use Copernicus or ESA logos, and do not imply endorsement (Art. 6(d)). Anyone who
redistributes these data is bound by the same obligations (Art. 6(e)); the root `NOTICE` carries them.
