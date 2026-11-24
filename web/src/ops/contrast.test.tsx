// Contrasting options on the card and the 'why not' answers (issue #174): an export with three options of one
// decision (two precedences), keyboard selection 1/2/3, and the card and drawer rendered from it.

import { renderToString } from "react-dom/server";
import { describe, expect, it } from "vitest";
import raw from "../../public/data/2025-08-20.json?raw";
import type { ChartData, Impact, Option, Preview, WhyNot } from "../types";
import { CardPanel } from "./CardPanel";
import {
  MAX_OPTIONS,
  contrastNote,
  contrastRows,
  optionForKey,
  precedences,
  whyNotList,
  whyNotState,
  whyNotText,
} from "./contrast";

const impact = (id: string, delta_s: number): Impact => ({
  id,
  name: id,
  class: "passenger",
  weight: 3,
  delta_s,
  exit_before_s: 0,
  exit_after_s: delta_s,
});

const option = (id: string, first: string | null, orders: string[], delta_min: number, impacts: Impact[], status: "PASS" | "FAIL" = "PASS"): Option => ({
  id,
  title: `${first ?? "hold"} first, option ${id}`,
  orders,
  trains: {},
  impacts,
  weighted_delay_delta_min: delta_min,
  verification: { status, gate: "G2", violations: status === "PASS" ? 0 : 1, warnings: 0, rules: [] },
  explanation: { why: `why ${id}`, why_not: `why not ${id}`, weights: { passenger: 3 }, hard_rules: [] },
  confidence: null,
  assumptions: [],
  first,
});

const A = option("A", "12503", ["SM/NWP: hold 20842 until 10:02", "SM/KBM: hold 20842 until 10:16"], -4.5, [impact("20842", 600), impact("12503", -900)]);
const B = option("B", "20842", ["SM/NWP: hold 12503 until 10:05"], 3.2, [impact("12503", 420)]);
const C = option("C", "12503", ["SM/NWP: hold 20842 until 10:02", "SM/KBM: hold 20842 until 10:16"], -4.5, [impact("20842", 600)], "FAIL");
const WHY: WhyNot[] = [
  { first: "20842", resource: "KBM~NWP", verified: true, reversed: true, cost_min: 7.7, fail: [], answer: "" },
  { first: "20830", resource: "KBM~NWP", verified: false, reversed: true, cost_min: null, fail: ["VER-XNG-01", "VER-HDW-02"], answer: "" },
  { first: "12503", resource: "KBM~NWP", verified: true, reversed: false, cost_min: 0, fail: [], answer: "" },
];

function exportOf(options: Option[], extra: Partial<Preview> = {}): ChartData {
  const base = JSON.parse(raw) as ChartData;
  return {
    ...base,
    preview: { ...base.preview, id: "R-3", demo: false, options, ...extra },
  };
}

const THREE = exportOf([A, B, C], {
  decision: { trains: ["12503", "20842"], resource: "KBM~NWP", options: 3, precedences: 2 },
  why_not: WHY,
});

describe("keyboard selection 1/2/3", () => {
  it("selects an option only while the card is open and the option exists", () => {
    expect(optionForKey("1", true, 3)).toBe(0);
    expect(optionForKey("2", true, 3)).toBe(1);
    expect(optionForKey("3", true, 3)).toBe(2);
    expect(optionForKey("3", true, 2)).toBeNull(); // no third option
    expect(optionForKey("2", true, 1)).toBeNull(); // a decision with one option
    expect(optionForKey("1", false, 3)).toBeNull(); // the card is closed
    expect(optionForKey("4", true, 3)).toBeNull();
    expect(optionForKey("a", true, 3)).toBeNull();
    expect(MAX_OPTIONS).toBe(3);
  });
});

describe("the contrast of the options", () => {
  it("gives each option its cost, who it serves first and the orders only it has", () => {
    const rows = contrastRows([A, B, C]);
    expect(rows.map((r) => r.key)).toEqual([1, 2, 3]);
    expect(rows[0]).toMatchObject({ id: "A", delta_min: -4.5, later: 1, earlier: 1, verifier: "PASS", first: "12503" });
    expect(rows[1]).toMatchObject({ id: "B", delta_min: 3.2, later: 1, earlier: 0, first: "20842" });
    expect(rows[1].onlyHere).toEqual(["SM/NWP: hold 12503 until 10:05"]);
    expect(rows[0].onlyHere).toEqual([]); // C has the same orders as A
    expect(rows[0].sameAs).toBe("C");
    expect(rows[2]).toMatchObject({ verifier: "FAIL", sameAs: "A" });
  });

  it("shows at most three options and tolerates options without who-goes-first", () => {
    const many = [A, B, C, { ...B, id: "D" }];
    expect(contrastRows(many)).toHaveLength(3);
    const plain = contrastRows([{ ...A, first: undefined }, { ...B, first: undefined }]);
    expect(plain.every((r) => r.first === null)).toBe(true);
  });

  it("counts the precedences, from the decision record or the options", () => {
    expect(precedences([A, B, C])).toBe(2);
    expect(precedences([A, C])).toBe(1);
    expect(precedences([{ ...A, first: undefined }])).toBeNull();
    expect(contrastNote(THREE.preview)).toBe("3 options, 2 different precedences: they differ in who goes first.");
    expect(contrastNote(exportOf([A, C]).preview)).toContain("one precedence: they differ in timing");
    expect(contrastNote(exportOf([A]).preview)).toBe("One option only: nothing to contrast.");
    expect(contrastNote(exportOf([{ ...A, first: null }, { ...B, first: null }]).preview)).toBe("2 options.");
  });
});

describe("why not", () => {
  it("lists the re-solves of the export and phrases each answer", () => {
    const list = whyNotList(THREE.preview);
    expect(list).toHaveLength(3);
    expect(whyNotList(THREE.preview, "20842")).toHaveLength(1);
    expect(whyNotList(exportOf([A]).preview)).toEqual([]); // no re-solve: tolerated
    expect(whyNotText(WHY[0])).toBe("20842 first at KBM~NWP: possible and verified, +7.7 passenger delay-min against the best plan");
    expect(whyNotText(WHY[1])).toBe("20830 first at KBM~NWP: not possible (VER-XNG-01, VER-HDW-02)");
    expect(whyNotText(WHY[2])).toBe("12503 first at KBM~NWP: the best plan keeps this order already");
    expect(WHY.map(whyNotState)).toEqual(["possible", "impossible", "same"]);
    expect(whyNotText({ ...WHY[0], cost_min: -1.25 })).toContain("−1.3 passenger delay-min"); // a cheaper other order
  });
});

// the server render puts comment markers between text nodes: drop them to read the text
const plain = (html: string) => html.replaceAll("<!-- -->", "");

const card = (data: ChartData, over: Partial<{ optionIdx: number; whyNot: boolean; drawer: boolean; phase: "none" | "card" }> = {}) =>
  plain(
    renderToString(
    <CardPanel
      data={data}
      option={data.preview.options[over.optionIdx ?? 0] ?? null}
      optionIdx={over.optionIdx ?? 0}
      phase={over.phase ?? "card"}
      now={36000}
      preview="after"
      drawer={over.drawer ?? false}
      whyNot={over.whyNot ?? false}
    />,
    ),
  );

describe("the card and the drawer", () => {
  it("lists the three options with their contrast and marks the chosen one", () => {
    const html = card(THREE, { optionIdx: 1 });
    for (const text of ["A: 12503 first, option A", "B: 20842 first, option B", "C: 12503 first, option C"]) expect(html).toContain(text);
    expect(html).toContain("3 options, 2 different precedences");
    expect(html).toContain("20842 first");
    expect(html).toContain('class="chosen"');
    expect(html).toContain('<li class="chosen"><b>2</b> B: '); // option 2 is the chosen one
    expect(html.match(/class="chosen"/g)).toHaveLength(1);
    expect(html).toContain("<kbd>Y</kbd>");
    expect(html).not.toContain("<h4>Why not</h4>"); // the answers wait for Y
  });

  it("shows the why-not answers after Y and the side-by-side table in the drawer", () => {
    const open = card(THREE, { whyNot: true });
    expect(open).toContain("<h4>Why not</h4>");
    expect(open).toContain("20842 first at KBM~NWP: possible and verified");
    expect(open).toContain("20830 first at KBM~NWP: not possible");
    const drawer = card(THREE, { drawer: true, optionIdx: 2 });
    expect(drawer).toContain("The options side by side");
    expect(drawer).toContain("Orders only this option has");
    expect(drawer).toContain("same orders as A");
    expect(drawer).toContain("Why not X first?");
  });

  it("tolerates a decision with one precedence and an export without re-solves", () => {
    const one = exportOf([A]);
    const html = card(one, { whyNot: true, drawer: true });
    expect(html).toContain("One option only: nothing to contrast.");
    expect(html).toContain("No re-solve in this export");
    expect(html).not.toContain("The options side by side");
    const sameFirst = card(exportOf([A, C], { why_not: [] }), { whyNot: true });
    expect(sameFirst).toContain("one precedence: they differ in timing");
  });

  it("still renders the committed day export (one demo option, no contrast fields)", () => {
    const base = JSON.parse(raw) as ChartData;
    const html = card(base, { optionIdx: 0 });
    expect(html).toContain("DEMO");
    expect(html).toContain("One option only");
  });

  it("keeps the closed card closed", () => {
    expect(card(THREE, { phase: "none" })).toContain("Press <kbd>P</kbd>");
  });
});
