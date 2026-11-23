// The management KPI view reads the committed evaluation export (a hand-made sample until the runner writes one;
// docs/kpi/evaluation-export.md, issue #173).

import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import raw from "../../public/data/evaluation.json?raw";
import { EvaluationView } from "./EvaluationView";
import {
  ALL,
  type EvalDoc,
  KPI_DEFS,
  arm,
  armsIn,
  cell,
  commonScenarios,
  dscChecks,
  isSample,
  kpisPresent,
  plansVerified,
  runLine,
  sampleBanner,
  scenarios,
  targetState,
  valueOf,
  verdict,
} from "./evaluation";

const doc = JSON.parse(raw) as EvalDoc;
const def = (key: string) => KPI_DEFS.find((d) => d.key === key)!;

describe("the committed evaluation export", () => {
  it("is the sample fixture of schema version 1 with scenarios and every arm", () => {
    expect(doc.schema_version).toBe(1);
    expect(isSample(doc)).toBe(true);
    expect(scenarios(doc).map((s) => s.id)).toEqual(["SCN-05", "SCN-20", "day 2025-08-01"]);
    expect(scenarios(doc)[0].title).toContain("CPP-SGDM");
    expect(armsIn(doc, ALL)).toEqual(["BL-0", "BL-1", "BL-2", "BL-3", "BL-5"]); // BL-5 has its own rows
    expect(armsIn(doc, "SCN-05")).toEqual(["BL-0", "BL-1", "BL-2", "BL-3"]);
    expect(runLine(doc)).toContain("network KTV-PSA-2025-08-r2");
  });

  it("offers the KPIs it has, the known ones in their fixed order", () => {
    const keys = kpisPresent(doc).map((d) => d.key);
    expect(keys[0]).toBe("A5_weighted_delay_train_min");
    expect(keys).toContain("D2_resolved_before_occurrence_pct");
    expect(keys).not.toContain("A4_max_arrival_delay_min"); // not in the file
    const extra: EvalDoc = { ...doc, rows: [{ ...doc.rows[0], kpis: { ...doc.rows[0].kpis, X9_new: 1 } }] };
    expect(kpisPresent(extra).at(-1)).toMatchObject({ key: "X9_new", label: "X9_new" });
  });
});

describe("values and differences", () => {
  it("reads one scenario or the mean over the scenarios that have the value", () => {
    expect(valueOf(doc, "SCN-05", "BL-2", "A5_weighted_delay_train_min")).toEqual({ value: 7100, n: 1 });
    // all scenarios: only those with every loop arm (SCN-05, SCN-20), so that the arms share the same scenarios
    expect(commonScenarios(doc)).toEqual(["SCN-05", "SCN-20"]);
    expect(valueOf(doc, ALL, "BL-0", "A5_weighted_delay_train_min")).toEqual({ value: (8830 + 5570) / 2, n: 2 });
    expect(valueOf(doc, ALL, "BL-2", "A5_weighted_delay_train_min")).toEqual({ value: (7100 + 4500) / 2, n: 2 });
    expect(valueOf(doc, "day 2025-08-01", "BL-2", "A5_weighted_delay_train_min").value).toBe(10300); // chosen alone
    expect(valueOf(doc, "SCN-05", "BL-0", "D1_median_conflict_lead_min")).toEqual({ value: null, n: 0 }); // null: not measured
    expect(valueOf(doc, ALL, "BL-5", "C2_freight_detention_h").value).toBe(283.1);
  });

  it("treats DSS as BL-2", () => {
    expect(arm("DSS")).toBe("BL-2");
    const renamed: EvalDoc = { ...doc, rows: doc.rows.map((r) => (r.arm === "BL-2" ? { ...r, arm: "DSS" } : r)) };
    expect(armsIn(renamed, "SCN-05")).toEqual(["BL-0", "BL-1", "BL-2", "BL-3"]);
    expect(valueOf(renamed, "SCN-05", "BL-2", "A5_weighted_delay_train_min").value).toBe(7100);
  });

  it("gives the difference from BL-0 and judges it by the direction of the KPI", () => {
    const a5 = cell(doc, "SCN-05", "BL-2", def("A5_weighted_delay_train_min"));
    expect(a5).toMatchObject({ value: 7100, delta: -1730, verdict: "better" });
    const a2 = cell(doc, "SCN-05", "BL-3", def("A2_section_punctuality_pct"));
    expect(a2).toMatchObject({ delta: 1.5, verdict: "better" });
    const c2 = cell(doc, "SCN-05", "BL-2", def("C2_freight_detention_h"));
    expect(c2.delta).toBe(-0.1);
    expect(cell(doc, "SCN-05", "BL-0", def("A5_weighted_delay_train_min")).delta).toBeNull(); // the baseline itself
    expect(cell(doc, "SCN-05", "BL-2", def("D1_median_conflict_lead_min")).delta).toBeNull(); // BL-0 has no value
    expect(verdict("lower", 0.001)).toBe("same");
    expect(verdict(null, 5)).toBeNull();
  });

  it("marks D1, D2, H1 and H2 against their absolute targets, not for BL-0 or BL-5", () => {
    expect(targetState(def("D2_resolved_before_occurrence_pct").target, 92)).toBe("met");
    expect(targetState(def("D2_resolved_before_occurrence_pct").target, 62.1)).toBe("missed");
    expect(targetState(def("H1_alerts_per_controller_hour").target, 6)).toBe("met");
    expect(targetState(def("H1_alerts_per_controller_hour").target, 6.5)).toBe("missed");
    expect(targetState(def("H2_flood_events").target, 0)).toBe("met");
    expect(targetState(def("H2_flood_events").target, 1)).toBe("missed");
    expect(targetState(undefined, 5)).toBeNull();
    expect(targetState(def("H2_flood_events").target, null)).toBeNull();
    expect(cell(doc, "SCN-05", "BL-2", def("D2_resolved_before_occurrence_pct")).target).toBe("met");
    expect(cell(doc, "SCN-05", "BL-1", def("D2_resolved_before_occurrence_pct")).target).toBe("missed");
    expect(cell(doc, "SCN-05", "BL-0", def("H2_flood_events")).target).toBeNull();
    // history has no difference from BL-0 (a different measure)
    expect(cell(doc, "day 2025-08-01", "BL-5", def("C2_freight_detention_h"))).toMatchObject({ value: 283.1, delta: null, verdict: null });
  });

  it("counts verified plans, summed over scenarios, and none for an arm without a loop", () => {
    expect(plansVerified(doc, "SCN-05", "BL-2")).toBe("70 of 74");
    expect(plansVerified(doc, ALL, "BL-2")).toBe(`${70 + 48} of ${74 + 50}`);
    expect(plansVerified(doc, "day 2025-08-01", "BL-2")).toBe("600 of 610");
    expect(plansVerified(doc, "SCN-05", "BL-0")).toBeNull();
  });
});

describe("acceptance criteria for the DSS arm", () => {
  it("computes DSC-01 to DSC-05 and DSC-09 for a scenario", () => {
    const checks = dscChecks(doc, "SCN-05");
    const by = (dsc: string, text: string) => checks.find((c) => c.dsc === dsc && c.text.includes(text))!;
    // BL-2 7100 against BL-1 8200: 13.4 % below (needs 15); against BL-0 8830: 19.6 % below (needs 25)
    expect(by("DSC-01", "BL-1")).toMatchObject({ value: "13.41 % below", met: false });
    expect(by("DSC-01", "BL-0")).toMatchObject({ value: "19.59 % below", met: false });
    expect(by("DSC-02", "points")).toMatchObject({ value: "+3 points", met: false });
    expect(by("DSC-03", "detention")).toMatchObject({ value: "+2.58 %", met: true }); // 15.9 against 15.5
    expect(by("DSC-04", "Resolved")).toMatchObject({ value: "92", met: true });
    expect(by("DSC-05", "Median conflict")).toMatchObject({ value: "41", met: true });
    expect(by("DSC-09", "Alerts")).toMatchObject({ value: "5.5", met: true });
    expect(by("DSC-09", "Flood")).toMatchObject({ value: "0", met: true });
  });

  it("compares the arms on the same scenarios when all scenarios are chosen", () => {
    const all = dscChecks(doc, ALL);
    // SCN-05 and SCN-20 only: BL-2 5800 against BL-1 6700 (13.43 % below) and BL-0 7200 (19.44 % below)
    expect(all.find((c) => c.text.includes("BL-1"))).toMatchObject({ value: "13.43 % below", met: false });
    expect(all.find((c) => c.text.includes("BL-0"))).toMatchObject({ value: "19.44 % below", met: false });
  });

  it("says n/a when the export lacks what a check needs", () => {
    const none: EvalDoc = { ...doc, rows: doc.rows.filter((r) => r.arm === "BL-0") };
    expect(dscChecks(none, ALL).every((c) => c.met === null && c.value === "-")).toBe(true);
  });
});

describe("the view", () => {
  it("shows the sample-data banner, the run, the arms, the KPIs, the deltas and the criteria", () => {
    const html = renderToString(<EvaluationView doc={doc} error="" onClose={() => {}} />);
    expect(html).toContain("SAMPLE DATA");
    expect(html).toContain("Management KPIs by arm");
    expect(html).toContain("KTV-PSA-2025-08-r2");
    for (const label of ["BL-0 no action", "BL-1 greedy", "BL-2 DSS", "BL-3 noisy DSS", "BL-5 history"]) expect(html).toContain(label);
    expect(html).toContain("Weighted delay");
    expect(html).toContain("Resolved before occurrence");
    expect(html).toContain("Plans verified (G0-G3)");
    expect(html).toContain("Acceptance criteria for the DSS arm");
    expect(html).toContain("DSC-04");
    expect(html).toContain("target met");
    expect(html).toContain("target missed");
    expect(html).toContain("Planned line block with overrun");
    expect(html).toContain("Mean over SCN-05, SCN-20");
  });

  it("writes the banner once, whether or not the note repeats it", () => {
    expect(sampleBanner(doc).startsWith("SAMPLE DATA.")).toBe(true);
    expect(sampleBanner(doc).match(/SAMPLE DATA/g)).toHaveLength(1);
    const plain: EvalDoc = { ...doc, run: { ...doc.run, notes: "Built by hand." } };
    expect(sampleBanner(plain)).toBe("SAMPLE DATA: invented numbers that exercise the view, not results. Built by hand.");
    expect(sampleBanner({ ...doc, run: { ...doc.run, notes: undefined } })).toBe("SAMPLE DATA: invented numbers that exercise the view, not results.");
  });

  it("has no sample banner for a real export, and shows an error when there is none", () => {
    const real: EvalDoc = { ...doc, run: { ...doc.run, sample: false } };
    expect(renderToString(<EvaluationView doc={real} error="" onClose={() => {}} />)).not.toContain("SAMPLE DATA");
    const html = renderToString(<EvaluationView doc={null} error="No evaluation export." onClose={() => {}} />);
    expect(html).toContain("No evaluation export.");
    expect(html).not.toContain("SAMPLE DATA");
  });
});
