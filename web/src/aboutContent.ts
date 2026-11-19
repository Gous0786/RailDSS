// Content of the About page (DR-0001 §3.3 condition 3, §4, §5.3; FR-DATA-05; issue #89).
// Kept as data so a test can check it against package.json and the attribution texts the licence review requires.

export const OSM_ATTRIBUTION = "© OpenStreetMap contributors";
export const OSM_COPYRIGHT_URL = "https://www.openstreetmap.org/copyright";
export const ODBL_URL = "https://opendatacommons.org/licenses/odbl/1-0/";

export const COPERNICUS_ATTRIBUTION =
  "produced using Copernicus WorldDEM-30 © DLR e.V. 2010-2014 and © Airbus Defence and Space GmbH 2014-2018 " +
  "provided under COPERNICUS by the European Union and ESA; all rights reserved";
export const COPERNICUS_DISCLAIMER =
  "The organisations in charge of the Copernicus programme by law or by delegation do not incur any liability " +
  "for any use of the Copernicus WorldDEM-30.";

export interface Library {
  name: string;
  version: string;
  licence: string;
  url: string;
  role: string;
}

/** Every production dependency of web/package.json (a test keeps this list in step). */
export const LIBRARIES: Library[] = [
  { name: "d3-scale", version: "4.0.3", licence: "ISC", url: "https://github.com/d3/d3-scale", role: "chart axes" },
  { name: "react", version: "19.3.1", licence: "MIT", url: "https://react.dev", role: "UI" },
  { name: "react-dom", version: "19.3.1", licence: "MIT", url: "https://react.dev", role: "UI" },
];

export const CHART_NOTE =
  "The control chart is an in-house component (d3-scale axes, HTML canvas drawing), the fallback chosen in " +
  "DR-0001 §3.4. It loads as its own chunk. The OSRD SpaceTimeChart (LGPL-3.0) is not used, so no LGPL duty applies.";

export const GRAFANA_NOTE =
  "Grafana (system health, analyst time series, evaluation runs) is an unmodified Grafana OSS image run as a " +
  "separate service (AGPL-3.0, DR-0001 §4). RailDSS only links to it. Controller and management views are in this app.";

/** Grafana address from the build environment (VITE_GRAFANA_URL); empty when no Grafana is deployed. */
export function grafanaUrl(env: Record<string, string | undefined>): string {
  const v = (env.VITE_GRAFANA_URL ?? "").trim();
  return /^https?:\/\//.test(v) ? v : "";
}
