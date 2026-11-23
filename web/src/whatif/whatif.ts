// Disruption and what-if view helpers (FR-UI-11; issue #172): the incident list and the plan comparison table,
// computed from a scenario export (`python -m raildss_orchestrator whatif SCN-05`).

import { hhmm } from "../chart/geometry";
import type { ChartData, Disruption, KpiSet, Option } from "../types";

export type Phase = "upcoming" | "active" | "ended";

export const TYPE_LABELS: Record<string, string> = {
  line_block: "Line block",
  signal_failure: "Signal / block-instrument failure",
  tsr: "Speed restriction",
  point_failure: "Loop out of use",
  loco_failure: "Loco failure",
  ohe_block: "OHE power block",
  ohe_trip: "OHE trip",
  crew_hours: "Crew out of hours",
  entry_delay: "Late entry",
  special_train: "Special train",
  exit_blocked: "Exit closed",
};

/** Where a disruption stands at the clock time `now`: before its start, between start and the real end, or over. */
export function phaseOf(d: Disruption, now: number): Phase {
  if (now < d.start_s) return "upcoming";
  return now < d.end_s ? "active" : "ended";
}

export function activeDisruptions(ds: Disruption[], now: number): Disruption[] {
  return ds.filter((d) => phaseOf(d, now) === "active");
}

export function label(d: Disruption): string {
  const what = TYPE_LABELS[d.type] ?? d.type;
  return d.location ? `${what}: ${d.location}${d.line ? ` (${d.line})` : ""}` : what;
}

/** "ETR 12:30 ±20 min (SM_ESTIMATE)", or "no ETR announced". */
export function etrLabel(d: Disruption): string {
  if (d.etr_s === null) return "no ETR announced";
  const sigma = d.etr_sigma_min ? ` ±${d.etr_sigma_min} min` : "";
  return `ETR ${hhmm(d.etr_s)}${sigma}${d.etr_source ? ` (${d.etr_source})` : ""}`;
}

/** How the announced ETR compares with the real end: the slip an analyst should know about, in minutes. */
export function etrSlipMin(d: Disruption): number | null {
  return d.etr_s === null ? null : Math.round((d.end_s - d.etr_s) / 60);
}

export interface Row {
  key: string;
  label: string;
  better: "lower" | "higher";
}

export const COMPARISON_ROWS: Row[] = [
  { key: "A5_weighted_delay_train_min", label: "Weighted delay (train-min)", better: "lower" },
  { key: "A2_section_punctuality_pct", label: "Section punctuality (%)", better: "higher" },
  { key: "A6_mean_exit_delay_min", label: "Mean exit delay (min)", better: "lower" },
  { key: "A6_p90_exit_delay_min", label: "P90 exit delay (min)", better: "lower" },
  { key: "A2_lop_count", label: "Trains late at exit", better: "lower" },
  { key: "C2_freight_detention_h", label: "Freight detention (h)", better: "lower" },
  { key: "F1_unplanned_stops", label: "Unplanned stops", better: "lower" },
  { key: "B1_trains_exited", label: "Trains exited", better: "higher" },
];

export type Verdict = "better" | "worse" | "same";

export interface Cell {
  value: number;
  delta: number; // against the baseline (0 for the baseline itself)
  verdict: Verdict;
}

export interface TableRow {
  row: Row;
  baseline: number;
  variants: Cell[];
}

export function verdict(row: Row, delta: number): Verdict {
  if (Math.abs(delta) < 0.005) return "same";
  return (delta < 0) === (row.better === "lower") ? "better" : "worse";
}

/** The plan comparison table: one row per KPI, the baseline and every variant (FR-UI-11). */
export function comparisonTable(baseline: KpiSet, options: Option[]): TableRow[] {
  return COMPARISON_ROWS.map((row) => {
    const base = baseline[row.key] ?? 0;
    return {
      row,
      baseline: base,
      variants: options.map((o) => {
        const value = o.kpis?.variant[row.key] ?? base;
        const delta = Math.round((value - base) * 100) / 100;
        return { value, delta, verdict: verdict(row, delta) };
      }),
    };
  });
}

/** Trains a variant makes later, earlier or leaves alone (among the trains it changes), and the worst one. */
export function impactSummary(o: Option): { later: number; earlier: number; worst: string | null } {
  const later = o.impacts.filter((i) => i.delta_s > 0);
  const worst = [...later].sort((a, b) => b.weight * b.delta_s - a.weight * a.delta_s)[0];
  return {
    later: later.length,
    earlier: o.impacts.filter((i) => i.delta_s < 0).length,
    worst: worst ? `${worst.id} +${Math.round(worst.delta_s / 60)} min` : null,
  };
}

/** Does this export carry a what-if (a scenario with disruptions and at least one variant)? */
export function hasWhatIf(data: ChartData | null): boolean {
  return !!data && (data.disruptions?.length ?? 0) > 0 && data.preview.options.length > 0;
}

/** The day the app opens on: the last real date of the index, not a scenario export. */
export function defaultDay(days: string[]): string {
  const dates = days.filter((d) => /^\d{4}-\d{2}-\d{2}$/.test(d));
  return dates[dates.length - 1] ?? days[days.length - 1] ?? "";
}
