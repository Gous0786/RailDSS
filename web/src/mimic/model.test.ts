import { describe, expect, it } from "vitest";
import raw from "../../public/data/2025-08-20.json";
import type { ChartData, Mimic } from "../types";
import { delayAt, delayColour, drawnKeys, icons, occupantsAt, shortName, trackY, width, xOf } from "./model";

const data = raw as unknown as ChartData;
const THRESHOLDS = { yellow: 5, amber: 15, red: 60, red_premium: 15 };

const tiny: Mimic = {
  stations: [
    { code: "A", name: "A", km: 0, halt: false, lines: [{ no: 1, name: null, kind: "main", csr_m: 700, platform: true, directions: "BOTH", in_use: true }] },
    { code: "B", name: "B", km: 10, halt: false, lines: [] },
  ],
  blocks: [{ id: "A-B", segment: "A~B", from: "A", to: "B", from_km: 0, to_km: 10, system: "ABS", lines: [{ line_id: "DN", direction: "DN", sub_blocks: 0 }] }],
  signals: [],
  occupancy: {
    resources: ["SL:A/1", "BL:A~B/DN"],
    intervals: [
      [0, 0, 100, 200],
      [1, 0, 200, 800],
      [0, 1, 500, null],
    ],
  },
};

describe("occupancy at a time", () => {
  it("holds a resource from its start until (not including) its end, or to the end of the run", () => {
    expect([...occupantsAt(tiny, 150)]).toEqual([["SL:A/1", [0]]]);
    expect([...occupantsAt(tiny, 200)]).toEqual([["BL:A~B/DN", [0]]]);
    expect(occupantsAt(tiny, 600).get("SL:A/1")).toEqual([1]);
    expect(occupantsAt(tiny, 99_999).get("SL:A/1")).toEqual([1]);
    expect(occupantsAt(tiny, 50).size).toBe(0);
  });
});

describe("delay colour", () => {
  it("follows the published badge thresholds, red earlier for premium trains", () => {
    expect(delayColour(null, THRESHOLDS)).toBe("none");
    expect(delayColour(4 * 60, THRESHOLDS)).toBe("ok");
    expect(delayColour(6 * 60, THRESHOLDS)).toBe("yellow");
    expect(delayColour(20 * 60, THRESHOLDS)).toBe("amber");
    expect(delayColour(20 * 60, THRESHOLDS, true)).toBe("red");
    expect(delayColour(61 * 60, THRESHOLDS)).toBe("red");
  });
});

describe("icon labels", () => {
  it("shows the train number of a passenger run and the first token of a freight load", () => {
    expect(shortName("12503@2025-08-19")).toBe("12503");
    expect(shortName("KSLKJEA_19082025115439")).toBe("KSLKJEA");
    expect(shortName("KNDJ 19082025144304")).toBe("KNDJ");
  });
});

describe("geometry", () => {
  it("places chainage left to right and lines on fixed rows", () => {
    expect(xOf(10)).toBeGreaterThan(xOf(0));
    expect(width(tiny)).toBeGreaterThan(xOf(10));
    expect(trackY("UP")).toBeLessThan(trackY("DN"));
    expect(trackY("KK")).toBeGreaterThan(trackY("DN"));
  });
});

describe("the committed day (generated from the published network and a twin run)", () => {
  const m = data.mimic as Mimic;
  it("carries the layout of the published network", () => {
    expect(data.version).toBeGreaterThanOrEqual(3);
    expect(m.stations.map((s) => s.code)).toEqual(data.stations.map((s) => s.code));
    expect(m.blocks.length).toBeGreaterThan(10);
    expect(m.signals.length).toBeGreaterThan(100);
    expect(m.stations.some((s) => s.lines.some((l) => l.kind === "loop" && (l.csr_m ?? 0) > 0))).toBe(true);
  });
  it("draws every resource the twin occupied", () => {
    const drawn = drawnKeys(m);
    expect(m.occupancy.resources.filter((r) => !drawn.has(r))).toEqual([]);
  });
  it("shows live occupancy and train icons at midday", () => {
    const occ = occupantsAt(m, 12 * 3600);
    expect(occ.size).toBeGreaterThan(5);
    const ic = icons(data, 12 * 3600, occ);
    expect(ic.length).toBeGreaterThan(5);
    expect(new Set(ic.map((i) => i.dir))).toEqual(new Set(["DN", "UP"]));
    for (const i of ic) expect(i.x).toBeGreaterThanOrEqual(xOf(0));
  });
  it("measures a timetabled train's delay against its booked line", () => {
    const train = data.trains.find((t) => t.booked.length > 2 && t.actual.length > 2)!;
    const t = train.actual[Math.floor(train.actual.length / 2)][0];
    expect(delayAt(train, t)).not.toBeNull();
  });
});
