// Pure helpers of the control chart: the time window, axis ticks and hit-testing (FR-UI-02).

import type { Point } from "../types";

export const DAY_S = 24 * 3600;
export const MIN_SPAN_S = 30 * 60; // zoom-in limit
export const MAX_SPAN_S = 14 * 3600; // zoom-out limit (now +/- 7 h, FR-UI-02)
export const DEFAULT_HALF_SPAN_S = 2 * 3600; // default view now -2 h ... +2 h

export interface Window {
  start: number;
  end: number;
}

/** The default view around `now`: -2 h ... +2 h. */
export function defaultWindow(now: number): Window {
  return clampWindow({ start: now - DEFAULT_HALF_SPAN_S, end: now + DEFAULT_HALF_SPAN_S });
}

/** Keep the span within the zoom limits and the window within the day (plus the spill-over past midnight). */
export function clampWindow(w: Window, dayEnd = DAY_S + 6 * 3600): Window {
  let span = Math.min(MAX_SPAN_S, Math.max(MIN_SPAN_S, w.end - w.start));
  const mid = (w.start + w.end) / 2;
  span = Math.min(span, dayEnd);
  let start = mid - span / 2;
  start = Math.max(0, Math.min(start, dayEnd - span));
  return { start, end: start + span };
}

/** Zoom by `factor` (< 1 zooms in) keeping the time under the cursor fixed. */
export function zoomWindow(w: Window, factor: number, anchor: number): Window {
  return clampWindow({ start: anchor - (anchor - w.start) * factor, end: anchor + (w.end - anchor) * factor });
}

export function panWindow(w: Window, deltaS: number): Window {
  return clampWindow({ start: w.start + deltaS, end: w.end + deltaS });
}

/** `HH:MM` of seconds since the start of the day (past midnight shows +1). */
export function hhmm(t: number): string {
  const day = Math.floor(t / DAY_S);
  const s = ((Math.round(t) % DAY_S) + DAY_S) % DAY_S;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const text = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
  return day > 0 ? `${text}+${day}` : text;
}

/** Tick step for the time axis so that ticks are at least `minPx` apart. */
export function timeTickStep(spanS: number, widthPx: number, minPx = 70): number {
  const steps = [300, 600, 900, 1800, 3600, 7200, 10800];
  return steps.find((s) => (s / spanS) * widthPx >= minPx) ?? steps[steps.length - 1];
}

/** Squared distance from point p to segment ab, in pixels. */
function segDist2(px: number, py: number, ax: number, ay: number, bx: number, by: number): number {
  const dx = bx - ax;
  const dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const u = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2));
  const x = ax + u * dx - px;
  const y = ay + u * dy - py;
  return x * x + y * y;
}

/** Index of the line nearest to (px, py) within `maxPx`, or -1. Lines are in screen coordinates. */
export function nearestLine(lines: Point[][], px: number, py: number, maxPx = 6): number {
  let best = -1;
  let bestD = maxPx * maxPx;
  lines.forEach((pts, i) => {
    for (let k = 1; k < pts.length; k++) {
      const d = segDist2(px, py, pts[k - 1][0], pts[k - 1][1], pts[k][0], pts[k][1]);
      if (d < bestD) {
        bestD = d;
        best = i;
      }
    }
  });
  return best;
}

/** The part of a line inside [start, end], with one point either side so it reaches the edges. */
export function visible(pts: Point[], start: number, end: number): Point[] {
  let i = 0;
  while (i < pts.length && pts[i][0] < start) i++;
  let j = pts.length - 1;
  while (j >= 0 && pts[j][0] > end) j--;
  if (i === pts.length || j < 0) return []; // wholly before or after the window
  return pts.slice(Math.max(0, i - 1), Math.min(pts.length, j + 2));
}
