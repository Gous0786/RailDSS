// KPI strip (FR-UI-10): A2, A5, B1, B6, C1, D1 and pending recommendations, each as value, 4 h sparkline and
// delta. Computed from the exported day over a rolling hour. Thresholds are the editable table below.

import type { ChartData, Train } from "../types";

const HOUR_S = 3600;
const SPARK_HOURS = 4; // sparkline: the last 4 h, one point per hour
const LATE_S = 15 * 60; // punctuality tolerance (kpi.punctuality_tolerance_min)

export type KpiId = "A2" | "A5" | "B1" | "B6" | "C1" | "D1" | "PEND";

export interface Kpi {
  id: KpiId;
  label: string;
  unit: string;
  value: number | null;
  spark: (number | null)[];
  delta: number | null; // value now minus the value an hour ago
  breach: boolean;
}

/** Neutral unless a threshold is breached. Values are placeholders until the KPI engine (#81) sets them. */
export const THRESHOLDS: Record<KpiId, { min?: number; max?: number }> = {
  A2: { min: 75 },
  A5: { max: 120 },
  B1: {},
  B6: { max: 6 },
  C1: { min: 25 },
  D1: { min: 15 },
  PEND: { max: 3 },
};

const LABELS: Record<KpiId, [string, string]> = {
  A2: ["A2 section punctuality", "%"],
  A5: ["A5 weighted delay", "train-min"],
  B1: ["B1 throughput", "trains/h"],
  B6: ["B6 detentions", "count/h"],
  C1: ["C1 goods speed", "km/h"],
  D1: ["D1 next conflict", "min"],
  PEND: ["Pending recommendations", ""],
};

const exitS = (t: Train) => (t.actual.length > 0 ? t.actual[t.actual.length - 1][0] : null);

function exitedIn(data: ChartData, t0: number, t1: number): Train[] {
  return data.trains.filter((t) => {
    const e = exitS(t);
    return e !== null && e > t0 && e <= t1;
  });
}

const timetabled = (t: Train) => t.entry_delay_s !== null && t.exit_delay_s !== null;

/** A2: of the timetabled trains that entered on time, the share that left on time (and the count that did not). */
export function a2(data: ChartData, t0: number, t1: number): number | null {
  const entered = exitedIn(data, t0, t1).filter((t) => timetabled(t) && (t.entry_delay_s as number) <= LATE_S);
  if (entered.length === 0) return null;
  return (100 * entered.filter((t) => (t.exit_delay_s as number) <= LATE_S).length) / entered.length;
}

/** A5: sum of class weight times the delay gained in the section, in train-minutes. */
export function a5(data: ChartData, t0: number, t1: number): number {
  return exitedIn(data, t0, t1)
    .filter(timetabled)
    .reduce((sum, t) => sum + (t.weight * Math.max(0, (t.exit_delay_s as number) - (t.entry_delay_s as number))) / 60, 0);
}

export function b1(data: ChartData, t0: number, t1: number): number {
  return (exitedIn(data, t0, t1).length * HOUR_S) / (t1 - t0);
}

/** B6: detentions (trains held for a resource) that started in the window. */
export function b6(data: ChartData, t0: number, t1: number): number {
  return data.alerts.filter((a) => a.type === "held" && a.time_s > t0 && a.time_s <= t1).length;
}

/** C1: average speed of the goods trains that left in the window. */
export function c1(data: ChartData, t0: number, t1: number): number | null {
  const goods = exitedIn(data, t0, t1).filter((t) => t.kind === "freight" && t.actual.length > 1);
  const km = goods.reduce((s, t) => s + Math.abs(t.actual[t.actual.length - 1][1] - t.actual[0][1]), 0);
  const hours = goods.reduce((s, t) => s + (t.actual[t.actual.length - 1][0] - t.actual[0][0]) / HOUR_S, 0);
  return hours > 0 ? km / hours : null;
}

/** D1: minutes to the next conflict (a train held for a resource); null when none is ahead. */
export function d1(data: ChartData, now: number): number | null {
  const next = data.alerts.filter((a) => a.type === "held" && a.time_s > now).sort((a, b) => a.time_s - b.time_s)[0];
  return next ? (next.time_s - now) / 60 : null;
}

function breach(id: KpiId, v: number | null): boolean {
  if (v === null) return false;
  const th = THRESHOLDS[id];
  return (th.min !== undefined && v < th.min) || (th.max !== undefined && v > th.max);
}

/** The strip at `now`. `pending` is the number of recommendations waiting for a decision. */
export function kpis(data: ChartData, now: number, pending: number): Kpi[] {
  const at = (id: KpiId, t: number): number | null => {
    const t0 = t - HOUR_S;
    switch (id) {
      case "A2":
        return a2(data, t0, t);
      case "A5":
        return a5(data, t0, t);
      case "B1":
        return b1(data, t0, t);
      case "B6":
        return b6(data, t0, t);
      case "C1":
        return c1(data, t0, t);
      case "D1":
        return d1(data, t);
      case "PEND":
        return pending;
    }
  };
  const ids: KpiId[] = ["A2", "A5", "B1", "B6", "C1", "D1", "PEND"];
  return ids.map((id) => {
    const value = at(id, now);
    const prev = at(id, now - HOUR_S);
    const spark = id === "PEND" ? [] : Array.from({ length: SPARK_HOURS + 1 }, (_, i) => at(id, now - (SPARK_HOURS - i) * HOUR_S));
    return {
      id,
      label: LABELS[id][0],
      unit: LABELS[id][1],
      value,
      spark,
      delta: id === "D1" || id === "PEND" || value === null || prev === null ? null : value - prev,
      breach: breach(id, value),
    };
  });
}
