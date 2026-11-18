// Train queue (FR-UI-07): the trains of the next 2 h with where they are, what is next and how late.
// Pure functions over the chart data; the React panel only renders them.

import type { Alert, ChartData, Point, Station, Train } from "../types";

export const HORIZON_S = 2 * 3600; // "next 2 h"
const AT_STATION_KM = 0.05; // within 50 m of a station chainage the train is "at" it
const NEAR_ALERT_S = 15 * 60; // an alert within this window of now flags the train

export type SortKey = "eta" | "train" | "class" | "dir" | "delay";

export interface QueueRow {
  id: string;
  name: string;
  class: string;
  weight: number;
  dir: "DN" | "UP";
  location: string;
  nextPoint: string; // station code
  scheduled: number | null; // booked time at the next point (timetabled trains)
  eta: number; // time at the next point in the twin run
  delayMin: number | null;
  load: string;
  crew: string;
  flags: string[]; // conflict, held
  nextAction: string;
  entryS: number; // time the train enters the section
}

/** Time at which a monotone-in-km line reaches `km` (linear between points); null if it never does. */
export function timeAtKm(pts: Point[], km: number): number | null {
  for (let i = 1; i < pts.length; i++) {
    const [t0, k0] = pts[i - 1];
    const [t1, k1] = pts[i];
    if (Math.min(k0, k1) <= km && km <= Math.max(k0, k1)) {
      return k1 === k0 ? t0 : t0 + ((km - k0) / (k1 - k0)) * (t1 - t0);
    }
  }
  return null;
}

/** Chainage of a line at time `t` (clamped to the first and last point), null for an empty line. */
export function kmAt(pts: Point[], t: number): number | null {
  if (pts.length === 0) return null;
  if (t <= pts[0][0]) return pts[0][1];
  for (let i = 1; i < pts.length; i++) {
    const [t0, k0] = pts[i - 1];
    const [t1, k1] = pts[i];
    if (t <= t1) return t1 === t0 ? k1 : k0 + ((t - t0) / (t1 - t0)) * (k1 - k0);
  }
  return pts[pts.length - 1][1];
}

export function directionOf(t: Train): "DN" | "UP" {
  const a = t.actual;
  return a.length > 1 && a[a.length - 1][1] < a[0][1] ? "UP" : "DN";
}

function whereIs(km: number, stations: Station[], dir: "DN" | "UP"): { text: string; next: Station | null } {
  const sorted = [...stations].sort((a, b) => a.km - b.km);
  const at = sorted.find((s) => Math.abs(s.km - km) <= AT_STATION_KM);
  const ahead = dir === "DN" ? sorted.filter((s) => s.km > km + AT_STATION_KM) : sorted.filter((s) => s.km < km - AT_STATION_KM).reverse();
  const behind = dir === "DN" ? sorted.filter((s) => s.km < km - AT_STATION_KM).reverse() : sorted.filter((s) => s.km > km + AT_STATION_KM);
  if (at) return { text: `at ${at.code}`, next: ahead[0] ?? null };
  const prev = behind[0];
  const next = ahead[0] ?? null;
  return { text: prev && next ? `${prev.code} → ${next.code}` : (next ? `before ${next.code}` : "leaving"), next };
}

function loadText(t: Train): string {
  return t.load ? `${t.load.wagons} wagons · ${t.load.tonnes} t` : "—";
}

/** Queue rows for the trains in the section now or entering within `horizonS`. */
export function queueRows(data: ChartData, now: number, horizonS = HORIZON_S, acked: ReadonlySet<string> = new Set()): QueueRow[] {
  const alertsOf = new Map<string, Alert[]>();
  for (const a of data.alerts) {
    if (acked.has(a.id)) continue;
    for (const id of a.trains) alertsOf.set(id, [...(alertsOf.get(id) ?? []), a]);
  }
  const option = data.preview.options[0];
  const changed = new Set(option ? Object.keys(option.trains) : []);
  const rows: QueueRow[] = [];
  for (const t of data.trains) {
    if (t.actual.length === 0) continue;
    const entryS = t.actual[0][0];
    const exitS = t.actual[t.actual.length - 1][0];
    if (entryS > now + horizonS || exitS < now) continue;
    const dir = directionOf(t);
    const km = kmAt(t.actual, now) ?? t.actual[0][1];
    const { text, next } = entryS > now ? { text: "not yet in section", next: nearestStation(data.stations, t.actual[0][1]) } : whereIs(km, data.stations, dir);
    const nextKm = next?.km ?? t.actual[t.actual.length - 1][1];
    const eta = timeAtKm(t.actual, nextKm) ?? exitS;
    const scheduled = t.booked.length > 0 ? timeAtKm(t.booked, nextKm) : null;
    const mine = alertsOf.get(t.id) ?? [];
    const near = (a: Alert) => Math.abs(a.time_s - now) <= NEAR_ALERT_S;
    const flags = [
      ...(mine.some((a) => near(a)) ? ["conflict"] : []),
      ...(mine.some((a) => a.type === "held" && near(a)) ? ["held"] : []),
    ];
    rows.push({
      id: t.id,
      name: t.name,
      class: t.class,
      weight: t.weight,
      dir,
      location: text,
      nextPoint: next?.code ?? "—",
      scheduled,
      eta,
      delayMin: scheduled === null ? null : (eta - scheduled) / 60,
      load: loadText(t),
      crew: "n/a",
      flags: [...new Set(flags)],
      nextAction: changed.has(t.id) ? `Rec ${data.preview.id}` : "—",
      entryS,
    });
  }
  return sortRows(rows, "eta");
}

function nearestStation(stations: Station[], km: number): Station | null {
  return stations.reduce<Station | null>((best, s) => (best === null || Math.abs(s.km - km) < Math.abs(best.km - km) ? s : best), null);
}

/** Default: by time of entry into the section, then priority (higher weight first). */
export function sortRows(rows: QueueRow[], key: SortKey, desc = false): QueueRow[] {
  const sign = desc ? -1 : 1;
  const cmp: Record<SortKey, (a: QueueRow, b: QueueRow) => number> = {
    eta: (a, b) => a.entryS - b.entryS || b.weight - a.weight || a.id.localeCompare(b.id),
    train: (a, b) => a.id.localeCompare(b.id),
    class: (a, b) => b.weight - a.weight || a.entryS - b.entryS,
    dir: (a, b) => a.dir.localeCompare(b.dir) || a.entryS - b.entryS,
    delay: (a, b) => (b.delayMin ?? -Infinity) - (a.delayMin ?? -Infinity) || a.entryS - b.entryS,
  };
  return [...rows].sort((a, b) => sign * cmp[key](a, b));
}

export interface QueueFilter {
  text: string; // train number or name
  cls: string; // "" for all
  dir: "" | "DN" | "UP";
  flaggedOnly: boolean;
}

export const NO_FILTER: QueueFilter = { text: "", cls: "", dir: "", flaggedOnly: false };

export function filterRows(rows: QueueRow[], f: QueueFilter): QueueRow[] {
  const q = f.text.trim().toLowerCase();
  return rows.filter(
    (r) =>
      (q === "" || r.id.toLowerCase().includes(q) || r.name.toLowerCase().includes(q)) &&
      (f.cls === "" || r.class === f.cls) &&
      (f.dir === "" || r.dir === f.dir) &&
      (!f.flaggedOnly || r.flags.length > 0),
  );
}
