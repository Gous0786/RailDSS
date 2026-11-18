import { describe, expect, it } from "vitest";
import raw from "../../public/data/2025-08-20.json";
import type { Alert, ChartData } from "../types";
import { acknowledge, alertGroups, leadMin, openCounts } from "./alerts";
import { acceptState, countdown, expiresIn, signedMin, topAffected, weightsUsed } from "./card";
import { kpis } from "./kpi";
import { NO_FILTER, filterRows, kmAt, queueRows, sortRows, timeAtKm } from "./queue";

const data = raw as unknown as ChartData;
const NOON = 12 * 3600;

describe("train queue (FR-UI-07)", () => {
  it("interpolates a line in both directions", () => {
    const dn: [number, number][] = [[0, 0], [600, 10]];
    const up: [number, number][] = [[0, 10], [600, 0]];
    expect(timeAtKm(dn, 5)).toBe(300);
    expect(timeAtKm(up, 5)).toBe(300);
    expect(timeAtKm(dn, 11)).toBeNull();
    expect(kmAt(dn, 300)).toBe(5);
    expect(kmAt(dn, 9999)).toBe(10);
    expect(kmAt([], 1)).toBeNull();
  });

  it("lists the trains of the next two hours, by section entry then priority", () => {
    const rows = queueRows(data, NOON);
    expect(rows.length).toBeGreaterThan(10);
    for (const r of rows) {
      const t = data.trains.find((x) => x.id === r.id)!;
      expect(t.actual[0][0]).toBeLessThanOrEqual(NOON + 2 * 3600);
      expect(t.actual[t.actual.length - 1][0]).toBeGreaterThanOrEqual(NOON);
    }
    for (let i = 1; i < rows.length; i++) {
      const [a, b] = [rows[i - 1], rows[i]];
      expect(a.entryS < b.entryS || (a.entryS === b.entryS && a.weight >= b.weight)).toBe(true);
    }
  });

  it("fills every column: class, direction, location, next point, scheduled vs ETA, delay, load", () => {
    const rows = queueRows(data, NOON);
    const pax = rows.find((r) => r.scheduled !== null)!;
    expect(pax.location).not.toBe("");
    expect(pax.nextPoint).toMatch(/^[A-Z]+$/);
    expect(pax.delayMin).toBeCloseTo((pax.eta - (pax.scheduled as number)) / 60, 6);
    expect(["DN", "UP"]).toContain(pax.dir);
    expect(pax.load).toBe("—");
    const frt = rows.find((r) => r.scheduled === null)!;
    expect(frt.delayMin).toBeNull();
    expect(frt.load).toMatch(/wagons · \d+ t/);
  });

  it("sorts by any column and filters by text, class, direction and flags", () => {
    const rows = queueRows(data, NOON);
    const byClass = sortRows(rows, "class");
    expect(byClass[0].weight).toBeGreaterThanOrEqual(byClass[byClass.length - 1].weight);
    const byDelay = sortRows(rows, "delay");
    expect((byDelay[0].delayMin ?? -Infinity)).toBeGreaterThanOrEqual(byDelay[byDelay.length - 1].delayMin ?? -Infinity);
    expect(sortRows(rows, "eta", true)[0].id).toBe(sortRows(rows, "eta")[rows.length - 1].id);
    const one = rows[0];
    expect(filterRows(rows, { ...NO_FILTER, text: one.id.slice(0, 5) }).map((r) => r.id)).toContain(one.id);
    expect(filterRows(rows, { ...NO_FILTER, cls: "freight_other" }).every((r) => r.class === "freight_other")).toBe(true);
    expect(filterRows(rows, { ...NO_FILTER, dir: "UP" }).every((r) => r.dir === "UP")).toBe(true);
    expect(filterRows(rows, { ...NO_FILTER, flaggedOnly: true }).every((r) => r.flags.length > 0)).toBe(true);
  });

  it("marks the trains an open recommendation would change", () => {
    const changed = Object.keys(data.preview.options[0].trains);
    const all = queueRows(data, data.preview.valid_until_s - 1800, 6 * 3600);
    expect(all.filter((r) => r.nextAction !== "—").every((r) => changed.includes(r.id))).toBe(true);
    expect(all.some((r) => r.nextAction !== "—")).toBe(true);
  });
});

describe("alerts and conflicts (FR-UI-08)", () => {
  const mk = (id: string, priority: Alert["priority"], type: string, location: string, time_s: number): Alert => ({
    id, priority, type, trains: [id], location, km: 0, time_s, lead_min: 0, recommendation: null, state: "open", raised_by: "test", detail: "",
  });
  const sample = [
    mk("a", "P2", "held", "CPP", 1000),
    mk("b", "P1", "held", "CPP", 1500), // same place within 30 min: one disruption
    mk("c", "P3", "held", "CPP", 9000), // same place, much later: another
    mk("d", "P2", "delay", "VZM", 500),
  ];

  it("groups one disruption as parent and children and orders P1 first", () => {
    const g = alertGroups(sample, "P3", new Set());
    expect(g.map((x) => x.parent.id)).toEqual(["b", "d", "c"]);
    expect(g[0].children.map((c) => c.id)).toEqual(["a"]);
  });

  it("filters by priority and acknowledges a whole group in one action", () => {
    expect(alertGroups(sample, "P2", new Set()).map((x) => x.parent.id)).toEqual(["b", "d"]);
    const g = alertGroups(sample, "P3", new Set());
    const acked = acknowledge(new Set(), g[0]);
    expect([...acked].sort()).toEqual(["a", "b"]);
    expect(alertGroups(sample, "P3", acked)[0].open).toBe(0);
    expect(openCounts(sample, 2000, acked)).toEqual({ P1: 0, P2: 1, P3: 0, P4: 0 });
    expect(leadMin(sample[0], 400)).toBe(10);
  });

  it("exports alerts with every field of the record", () => {
    expect(data.alerts.length).toBeGreaterThan(0);
    for (const a of data.alerts.slice(0, 50)) {
      expect(a.id).toMatch(/^A-\d{3}$/);
      expect(["P1", "P2", "P3", "P4"]).toContain(a.priority);
      expect(a.trains.length).toBeGreaterThan(0);
      expect(a.raised_by).not.toBe("");
    }
  });
});

describe("recommendation card (FR-UI-09, FR-HITL-13, OD-04)", () => {
  const option = data.preview.options[0];

  it("shows the three most affected trains, weighted, worst first", () => {
    const top = topAffected(option);
    expect(top.length).toBe(Math.min(3, option.impacts.length));
    const score = (i: (typeof top)[number]) => i.weight * Math.abs(i.delta_s);
    for (let i = 1; i < top.length; i++) expect(score(top[i - 1])).toBeGreaterThanOrEqual(score(top[i]));
  });

  it("enables Accept only once the card shows them and the verifier passed", () => {
    expect(acceptState(option, false).enabled).toBe(false);
    expect(acceptState(option, true).enabled).toBe(true);
    expect(acceptState(option, true, true)).toEqual({ enabled: false, reason: "the recommendation has expired" });
    const failed = { ...option, verification: { ...option.verification, status: "FAIL" as const } };
    expect(acceptState(failed, true)).toEqual({ enabled: false, reason: "the verifier did not pass this option" });
  });

  it("lists the weights and any hard rule applied", () => {
    expect(data.policy.weights_in_explanations).toBe(true);
    expect(Object.keys(option.explanation.weights).length).toBe(Object.keys(data.policy.weights).length);
    expect(option.explanation.hard_rules.map((r) => r.rule)).toEqual(data.policy.hard_rules);
    const used = weightsUsed(option, data.policy.weights);
    expect(used.length).toBe(Object.keys(data.policy.weights).length);
    expect(new Set(used.slice(0, 2).map(([k]) => k)).size).toBe(2);
  });

  it("counts down to the expiry and formats impacts", () => {
    expect(expiresIn(1000, 400)).toBe(600);
    expect(expiresIn(1000, 2000)).toBe(0);
    expect(countdown(75)).toBe("01:15");
    expect(countdown(3725)).toBe("1:02:05");
    expect(signedMin(930)).toBe("+15.5 min");
    expect(signedMin(-60)).toBe("−1.0 min");
    expect(option.verification.rules.length).toBeGreaterThan(5);
  });
});

describe("KPI strip (FR-UI-10)", () => {
  const strip = kpis(data, NOON + 3 * 3600, 1);

  it("has the seven tiles with a sparkline of the last 4 h", () => {
    expect(strip.map((k) => k.id)).toEqual(["A2", "A5", "B1", "B6", "C1", "D1", "PEND"]);
    for (const k of strip.filter((x) => !["PEND"].includes(x.id))) expect(k.spark.length).toBe(5);
    expect(strip.find((k) => k.id === "PEND")?.value).toBe(1);
  });

  it("computes the values from the exported trains", () => {
    const by = Object.fromEntries(strip.map((k) => [k.id, k]));
    expect(by.B1.value).toBeGreaterThan(0);
    expect(by.A2.value).toBeGreaterThanOrEqual(0);
    expect(by.A2.value).toBeLessThanOrEqual(100);
    expect(by.A5.value).toBeGreaterThanOrEqual(0);
    expect(by.C1.value).toBeGreaterThan(10);
    expect(by.C1.value).toBeLessThan(130);
  });

  it("stays neutral unless a threshold is breached", () => {
    const calm = kpis(data, NOON, 0).find((k) => k.id === "PEND")!;
    expect(calm.breach).toBe(false);
    expect(kpis(data, NOON, 9).find((k) => k.id === "PEND")!.breach).toBe(true);
  });
});
