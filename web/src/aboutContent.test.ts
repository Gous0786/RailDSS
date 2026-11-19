import { describe, expect, it } from "vitest";
import notice from "../../NOTICE?raw";
import pkg from "../package.json";
import { COPERNICUS_ATTRIBUTION, LIBRARIES, OSM_ATTRIBUTION, OSM_COPYRIGHT_URL, grafanaUrl } from "./aboutContent";

describe("About page content (issue #89)", () => {
  it("lists every production dependency at its pinned version", () => {
    const deps: Record<string, string> = pkg.dependencies;
    expect(LIBRARIES.map((l) => l.name).sort()).toEqual(Object.keys(deps).sort());
    for (const l of LIBRARIES) expect(deps[l.name]).toBe(l.version);
  });

  it("pins production dependencies exactly (no ranges)", () => {
    for (const v of Object.values<string>(pkg.dependencies)) expect(v).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it("carries the attribution texts of DR-0001 §5, identical to NOTICE", () => {
    expect(OSM_ATTRIBUTION).toBe("© OpenStreetMap contributors");
    expect(OSM_COPYRIGHT_URL).toBe("https://www.openstreetmap.org/copyright");
    expect(notice).toContain(OSM_ATTRIBUTION);
    expect(notice).toContain(COPERNICUS_ATTRIBUTION);
  });

  it("links Grafana only for an http(s) address", () => {
    expect(grafanaUrl({})).toBe("");
    expect(grafanaUrl({ VITE_GRAFANA_URL: "javascript:alert(1)" })).toBe("");
    expect(grafanaUrl({ VITE_GRAFANA_URL: " http://localhost:3000 " })).toBe("http://localhost:3000");
  });
});
