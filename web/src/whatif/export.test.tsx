// The view loads from the committed export of a disruption scenario (issue #172): SCN-05, a DN line blocked on
// CPP-SGDM, with the BL-1 and DSS variants (`python -m raildss_orchestrator whatif SCN-05`).

import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import raw from "../../public/data/SCN-05.json?raw";
import type { ChartData } from "../types";
import { WhatIfView } from "./WhatIfView";
import { COMPARISON_ROWS, activeDisruptions, comparisonTable, hasWhatIf, impactSummary, label, phaseOf } from "./whatif";

const data = JSON.parse(raw) as ChartData;

describe("the committed SCN-05 export", () => {
  it("is a chart document with the incident and one variant per arm", () => {
    expect(data.format).toBe("raildss-chart");
    expect(data.scenario?.id).toBe("SCN-05");
    expect(data.trains.length).toBeGreaterThan(100);
    expect(data.disruptions).toHaveLength(1);
    expect(data.preview.options.map((o) => o.arm)).toEqual(["BL-1", "DSS"]);
    expect(data.whatif?.arms).toEqual(["BL-1", "DSS"]);
    expect(hasWhatIf(data)).toBe(true);
    expect(data.preview.demo).toBe(false);
  });

  it("lists the line block with its location, times, ETR and affected line", () => {
    const [d] = data.disruptions!;
    expect(d.type).toBe("line_block");
    expect(label(d)).toBe("Line block: CPP-SGDM (DN)");
    expect(d.start_s).toBe(36000);
    expect(d.end_s).toBeGreaterThan(d.start_s + 2 * 3600);
    expect(d.etr_s).toBeGreaterThan(d.end_s); // the announced ETR is after the real end in this scenario
    expect(d.lines_affected).toEqual(["CPP-SGDM/DN"]);
    expect(phaseOf(d, 35000)).toBe("upcoming");
    expect(activeDisruptions(data.disruptions!, 40000)).toHaveLength(1);
  });

  it("has verified variants whose changed trains exist in the day and whose KPIs cover every comparison row", () => {
    const ids = new Set(data.trains.map((t) => t.id));
    for (const o of data.preview.options) {
      expect(o.verification.status).toBe("PASS");
      expect(Object.keys(o.trains).every((id) => ids.has(id))).toBe(true);
      expect(o.impacts.length).toBe(Object.keys(o.trains).length);
      for (const row of COMPARISON_ROWS) expect(o.kpis!.variant[row.key]).toBeTypeOf("number");
    }
    const [bl1, dss] = data.preview.options;
    expect(impactSummary(bl1)).toEqual({ later: 0, earlier: 0, worst: null }); // the greedy loop changes no train line here
    expect(Object.keys(dss.trains).length).toBeGreaterThan(0);
  });

  it("gives a comparison table with the baseline and both variants", () => {
    const rows = comparisonTable(data.whatif!.baseline.kpis, data.preview.options);
    expect(rows).toHaveLength(COMPARISON_ROWS.length);
    expect(rows.every((r) => r.variants.length === 2)).toBe(true);
    expect(rows[0].baseline).toBe(data.whatif!.baseline.kpis[rows[0].row.key]);
  });

  it("renders the view with the sandbox banner, the incident, the variants and the comparison", () => {
    const html = renderToString(
      <WhatIfView data={data} variant={1} onVariant={() => {}} mode="after" onMode={() => {}} now={40000} onClose={() => {}} />,
    );
    expect(html).toContain("SANDBOX");
    expect(html).toContain("Line block: CPP-SGDM (DN)");
    expect(html).toContain("active"); // at 11:06 the block is on
    expect(html).toContain("Plan comparison");
    expect(html).toContain("Weighted delay (train-min)");
    expect(html).toContain("BL-1: the loop with the greedy planner");
    expect(html).toContain("DSS: the loop with the CP-SAT rolling horizon");
    expect(html).toContain("Verifier:");
    expect(html).toContain("nothing here changes the");
  });

  it("renders the baseline alone when no variant is chosen", () => {
    const html = renderToString(
      <WhatIfView data={data} variant={-1} onVariant={() => {}} mode="off" onMode={() => {}} now={30000} onClose={() => {}} />,
    );
    expect(html).not.toContain("Verifier:");
    expect(html).toContain("upcoming");
  });
});
