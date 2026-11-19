// Schematic mimic model (FR-UI-06, issue #87): pure functions over the exported layout and occupancy.
// The SVG is generated from these; nothing is drawn by hand.
import { kmAt, timeAtKm } from "../ops/queue";
import type { ChartData, Mimic, Train } from "../types";

export const PX_PER_KM = 24;
export const MARGIN_X = 40;
export const STATION_W = 44; // width of a station's line bars (px)
export const TRACK_Y: Record<string, number> = { UP: 70, MID: 92, DN: 114 };
export const OTHER_TRACK_Y = 136; // any other block line id (KK, 1, ...)
export const LINES_TOP = 170; // first station-line bar
export const LINE_PITCH = 16; // vertical distance between station-line bars

export type Colour = "ok" | "yellow" | "amber" | "red" | "none";

export function xOf(km: number): number {
  return MARGIN_X + km * PX_PER_KM;
}

export function trackY(lineId: string): number {
  return TRACK_Y[lineId] ?? OTHER_TRACK_Y;
}

export function width(m: Mimic): number {
  const last = Math.max(...m.stations.map((s) => s.km), 0);
  return xOf(last) + MARGIN_X;
}

export function height(m: Mimic): number {
  const most = Math.max(...m.stations.map((s) => s.lines.length), 0);
  return LINES_TOP + most * LINE_PITCH + 30;
}

/** Resource label -> indices of the trains holding it at time `t`. */
export function occupantsAt(m: Mimic, t: number): Map<string, number[]> {
  const out = new Map<string, number[]>();
  for (const [r, train, start, end] of m.occupancy.intervals) {
    if (start > t) break; // intervals are sorted by start
    if (end !== null && end <= t) continue;
    const label = m.occupancy.resources[r];
    const list = out.get(label) ?? [];
    list.push(train);
    out.set(label, list);
  }
  return out;
}

export const stationLineKey = (op: string, no: number): string => `SL:${op}/${no}`;
export const blockLineKey = (segment: string, line: string): string => `BL:${segment}/${line}`;
export const subBlockKey = (segment: string, line: string, n: number): string => `SB:${segment}/${line}#${n}`;

/** Every resource a drawn element stands for (to check that the layout covers the occupancy). */
export function drawnKeys(m: Mimic): Set<string> {
  const keys = new Set<string>();
  for (const s of m.stations) for (const l of s.lines) keys.add(stationLineKey(s.code, l.no));
  for (const b of m.blocks)
    for (const l of b.lines) {
      keys.add(blockLineKey(b.segment, l.line_id));
      for (let n = 1; n <= l.sub_blocks; n++) keys.add(subBlockKey(b.segment, l.line_id, n));
    }
  return keys;
}

/** Delay of a timetabled train at time `t` (s): actual time minus booked time at its current chainage. */
export function delayAt(train: Train, t: number): number | null {
  if (!train.booked.length || !train.actual.length) return null;
  const km = kmAt(train.actual, t);
  if (km === null) return null;
  const booked = timeAtKm(train.booked, km);
  return booked === null ? null : t - booked;
}

/** Delay colour per the published badge thresholds (ui.delay_badge_thresholds_min); premium turns red earlier. */
export function delayColour(delayS: number | null, thresholds: Record<string, number>, premium = false): Colour {
  if (delayS === null) return "none";
  const min = delayS / 60;
  const red = premium ? (thresholds.red_premium ?? thresholds.red) : thresholds.red;
  if (min >= red) return "red";
  if (min >= thresholds.amber) return "amber";
  if (min >= thresholds.yellow) return "yellow";
  return "ok";
}

export interface Icon {
  train: number;
  id: string;
  label: string; // train number
  dir: "DN" | "UP";
  x: number;
  y: number;
  colour: Colour;
  where: string; // the resource it holds
}

/** Train number for an icon: a passenger run's number, a freight load's first token. */
export function shortName(id: string): string {
  return id.split(/[@_ ]/)[0];
}

/** Train icons at time `t`: one per train holding a resource, on the line it holds. */
export function icons(data: ChartData, t: number, occ: Map<string, number[]>): Icon[] {
  const m = data.mimic;
  if (!m) return [];
  const stationIdx = new Map(m.stations.map((s) => [s.code, s]));
  const placed = new Map<number, Icon>();
  for (const [label, trains] of occ) {
    for (const i of trains) {
      if (placed.has(i)) continue;
      const train = data.trains[i];
      const km = kmAt(train.actual, t);
      const a = train.actual;
      const dir: "DN" | "UP" = a.length > 1 && a[a.length - 1][1] < a[0][1] ? "UP" : "DN";
      let x = xOf(km ?? 0);
      let y = TRACK_Y[dir];
      const [kind, rest] = label.split(":");
      if (kind === "SL") {
        const [op, no] = rest.split("/");
        const st = stationIdx.get(op);
        const row = st ? st.lines.findIndex((l) => String(l.no) === no) : -1;
        if (st) x = xOf(st.km);
        if (row >= 0) y = LINES_TOP + row * LINE_PITCH;
      } else {
        y = trackY(rest.split("/")[1].split("#")[0]);
      }
      const premium = train.class === "premium";
      placed.set(i, {
        train: i,
        id: train.id,
        label: shortName(train.id),
        dir,
        x,
        y,
        colour: delayColour(delayAt(train, t), data.ui.delay_badge_thresholds_min, premium),
        where: label,
      });
    }
  }
  return [...placed.values()].sort((p, q) => p.x - q.x);
}
