import { describe, expect, it } from "vitest";
import type { Point } from "../types";
import {
  MAX_SPAN_S,
  MIN_SPAN_S,
  clampWindow,
  defaultWindow,
  hhmm,
  nearestLine,
  panWindow,
  timeTickStep,
  visible,
  zoomWindow,
} from "./geometry";

describe("time window", () => {
  it("defaults to now -2 h ... +2 h", () => {
    expect(defaultWindow(12 * 3600)).toEqual({ start: 10 * 3600, end: 14 * 3600 });
  });
  it("keeps zoom within limits and the window within the day", () => {
    const w = zoomWindow(defaultWindow(12 * 3600), 0.01, 12 * 3600);
    expect(w.end - w.start).toBe(MIN_SPAN_S);
    const out = zoomWindow(defaultWindow(12 * 3600), 100, 12 * 3600);
    expect(out.end - out.start).toBe(MAX_SPAN_S);
    expect(panWindow(defaultWindow(3600), -10 * 3600).start).toBe(0);
    expect(clampWindow({ start: -50, end: 3000 }).start).toBe(0);
  });
  it("zooms around the anchor", () => {
    const w = zoomWindow({ start: 0, end: 7200 }, 0.5, 3600);
    expect(w).toEqual({ start: 1800, end: 5400 });
  });
});

describe("axis", () => {
  it("formats times, past midnight with +1", () => {
    expect(hhmm(0)).toBe("00:00");
    expect(hhmm(13 * 3600 + 5 * 60)).toBe("13:05");
    expect(hhmm(24 * 3600 + 30 * 60)).toBe("00:30+1");
  });
  it("chooses a readable tick step", () => {
    expect(timeTickStep(4 * 3600, 1200)).toBe(900);
    expect(timeTickStep(14 * 3600, 600)).toBe(7200);
    expect(timeTickStep(14 * 3600, 300)).toBe(10800);
  });
});

describe("lines", () => {
  const a: Point[] = [
    [0, 0],
    [100, 100],
  ];
  const b: Point[] = [
    [0, 50],
    [100, 50],
  ];
  it("finds the nearest line within tolerance", () => {
    expect(nearestLine([a, b], 80, 52)).toBe(1);
    expect(nearestLine([a, b], 10, 11)).toBe(0);
    expect(nearestLine([a, b], 90, 10)).toBe(-1);
  });
  it("clips a line to the window with one point either side", () => {
    const pts: Point[] = [
      [0, 0],
      [10, 1],
      [20, 2],
      [30, 3],
    ];
    expect(visible(pts, 12, 18)).toEqual([
      [10, 1],
      [20, 2],
    ]);
    expect(visible(pts, 40, 50)).toEqual([]);
  });
});
