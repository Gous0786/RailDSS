import { describe, expect, it } from "vitest";
import type { ChartData, Disruption, Option } from "../types";
import {
  COMPARISON_ROWS,
  activeDisruptions,
  comparisonTable,
  defaultDay,
  etrLabel,
  etrSlipMin,
  hasWhatIf,
  impactSummary,
  label,
  phaseOf,
  verdict,
} from "./whatif";

const block: Disruption = {
  id: "SCN05-DN-CPP-SGDM",
  type: "line_block",
  cause_code: null,
  location: "CPP-SGDM",
  line: "DN",
  km_from: 66.1,
  km_to: 80.3,
  start_s: 36000,
  end_s: 36000 + 9000,
  etr_s: 36000 + 9000 + 1800,
  etr_sigma_min: 20,
  etr_source: "SM_ESTIMATE",
  lines_affected: ["CPP-SGDM/DN"],
  linked_trains: [],
  single_line_working: true,
  status: "scripted",
  owner: "section master (scripted scenario)",
  note: "",
};
const entry: Disruption = { ...block, id: "late", type: "entry_delay", location: "", line: null, start_s: 54000, end_s: 55800, etr_s: null, etr_sigma_min: null, etr_source: null };

const option = (id: string, variant: Record<string, number>, impacts: Option["impacts"] = []): Option => ({
  id,
  title: id,
  orders: [],
  trains: {},
  impacts,
  weighted_delay_delta_min: 0,
  verification: { status: "PASS", gate: "", violations: 0, warnings: 0, rules: [] },
  explanation: { why: "", why_not: "", weights: {}, hard_rules: [] },
  confidence: null,
  assumptions: [],
  arm: id,
  kpis: { baseline: {}, variant },
});

describe("incidents", () => {
  it("are upcoming before their start, active until the real end, then ended", () => {
    expect(phaseOf(block, 35999)).toBe("upcoming");
    expect(phaseOf(block, 36000)).toBe("active");
    expect(phaseOf(block, 44999)).toBe("active");
    expect(phaseOf(block, 45000)).toBe("ended");
    expect(activeDisruptions([block, entry], 37000)).toEqual([block]);
    expect(activeDisruptions([block, entry], 60000)).toEqual([]);
  });

  it("are labelled by type, location and line", () => {
    expect(label(block)).toBe("Line block: CPP-SGDM (DN)");
    expect(label(entry)).toBe("Late entry");
    expect(label({ ...block, type: "something_new", line: null })).toBe("something_new: CPP-SGDM");
  });

  it("show the ETR with its uncertainty and source, and how far the real end is from it", () => {
    expect(etrLabel(block)).toBe("ETR 13:00 ±20 min (SM_ESTIMATE)");
    expect(etrLabel(entry)).toBe("no ETR announced");
    expect(etrSlipMin(block)).toBe(-30); // the real end is 30 min before the announced ETR
    expect(etrSlipMin(entry)).toBeNull();
  });
});

describe("plan comparison", () => {
  it("judges each KPI by the direction in which it is better", () => {
    const lower = COMPARISON_ROWS.find((r) => r.key === "A5_weighted_delay_train_min")!;
    const higher = COMPARISON_ROWS.find((r) => r.key === "A2_section_punctuality_pct")!;
    expect(verdict(lower, -10)).toBe("better");
    expect(verdict(lower, 10)).toBe("worse");
    expect(verdict(higher, 2)).toBe("better");
    expect(verdict(higher, -2)).toBe("worse");
    expect(verdict(lower, 0.001)).toBe("same");
  });

  it("has a row per KPI with the baseline and every variant against it", () => {
    const baseline = { A5_weighted_delay_train_min: 1000, A2_section_punctuality_pct: 60, C2_freight_detention_h: 10 };
    const a = option("BL-1", { A5_weighted_delay_train_min: 900, A2_section_punctuality_pct: 58, C2_freight_detention_h: 10 });
    const b = option("DSS", { A5_weighted_delay_train_min: 1100, A2_section_punctuality_pct: 63, C2_freight_detention_h: 10 });
    const rows = comparisonTable(baseline, [a, b]);
    expect(rows).toHaveLength(COMPARISON_ROWS.length);
    const a5 = rows.find((r) => r.row.key === "A5_weighted_delay_train_min")!;
    expect(a5.baseline).toBe(1000);
    expect(a5.variants.map((c) => [c.value, c.delta, c.verdict])).toEqual([
      [900, -100, "better"],
      [1100, 100, "worse"],
    ]);
    const a2 = rows.find((r) => r.row.key === "A2_section_punctuality_pct")!;
    expect(a2.variants.map((c) => c.verdict)).toEqual(["worse", "better"]);
    expect(rows.find((r) => r.row.key === "C2_freight_detention_h")!.variants.map((c) => c.verdict)).toEqual(["same", "same"]);
  });

  it("summarises the trains a variant makes later or earlier and names the worst", () => {
    const impacts = [
      { id: "T1", name: "T1", class: "freight_other", weight: 1, delta_s: 600, exit_before_s: 0, exit_after_s: 600 },
      { id: "T2", name: "T2", class: "passenger", weight: 3, delta_s: 300, exit_before_s: 0, exit_after_s: 300 },
      { id: "T3", name: "T3", class: "passenger", weight: 3, delta_s: -900, exit_before_s: 0, exit_after_s: -900 },
    ];
    expect(impactSummary(option("A", {}, impacts))).toEqual({ later: 2, earlier: 1, worst: "T2 +5 min" }); // weighted: 900 > 600
    expect(impactSummary(option("A", {}))).toEqual({ later: 0, earlier: 0, worst: null });
  });
});

describe("the app's data", () => {
  it("opens on the last real day, not on a scenario export", () => {
    expect(defaultDay(["2025-08-12", "2025-08-20", "SCN-05"])).toBe("2025-08-20");
    expect(defaultDay(["SCN-05"])).toBe("SCN-05");
    expect(defaultDay([])).toBe("");
  });

  it("offers the what-if view only for a scenario export with disruptions and variants", () => {
    const base = { disruptions: [block], preview: { options: [option("A", {})] } } as unknown as ChartData;
    expect(hasWhatIf(base)).toBe(true);
    expect(hasWhatIf({ ...base, disruptions: [] })).toBe(false);
    expect(hasWhatIf({ ...base, preview: { ...base.preview, options: [] } })).toBe(false);
    expect(hasWhatIf({ preview: { options: [option("A", {})] } } as unknown as ChartData)).toBe(false);
    expect(hasWhatIf(null)).toBe(false);
  });
});
