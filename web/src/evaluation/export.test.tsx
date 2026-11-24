// The management view reads the committed evaluation export: the multi-day replay of issue #169.

import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import raw from "../../public/data/evaluation.json?raw";
import { EvaluationView } from "./EvaluationView";
import { ALL, type EvalDoc, armsIn, cell, commonScenarios, dscChecks, isSample, KPI_DEFS, scenarios } from "./evaluation";

const doc = JSON.parse(raw) as EvalDoc;

describe("the committed export (the multi-day replay)", () => {
  it("is a real export of five real days with BL-0, BL-1, BL-2 and the history", () => {
    expect(isSample(doc)).toBe(false);
    expect(doc.run.scenario_set).toBe("real-days-multi-day");
    expect(doc.run.window).toEqual({ from_h: 6, to_h: 12 });
    expect(scenarios(doc).map((s) => s.id)).toEqual([
      "day 2025-08-05",
      "day 2025-08-12",
      "day 2025-08-15",
      "day 2025-08-20",
      "day 2025-08-22",
    ]);
    expect(armsIn(doc, "day 2025-08-12")).toEqual(["BL-0", "BL-1", "BL-2", "BL-5"]);
    expect(commonScenarios(doc)).toHaveLength(5); // every day has both loop arms
  });

  it("has the DSS weighted delay below BL-0 on most days", () => {
    const a5 = KPI_DEFS.find((d) => d.key === "A5_weighted_delay_train_min")!;
    const deltas = scenarios(doc).map((s) => cell(doc, s.id, "BL-2", a5).delta as number);
    expect(deltas.filter((d) => d < 0).length).toBeGreaterThanOrEqual(3);
    const checks = dscChecks(doc, ALL);
    expect(checks.find((c) => c.dsc === "DSC-01")).toBeDefined();
  });

  it("renders without the sample banner, with the days in the scenario selector", () => {
    const html = renderToString(<EvaluationView doc={doc} error="" onClose={() => {}} />);
    expect(html).not.toContain("SAMPLE DATA");
    expect(html).toContain("real-days-multi-day");
    expect(html).toContain("Real day 2025-08-12, loop window 6-12 h");
    expect(html).toContain("Mean over day 2025-08-05, day 2025-08-12");
  });
});
