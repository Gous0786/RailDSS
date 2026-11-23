// Management KPI view helpers (FR-UI-13 family, issue #173): the evaluation export of docs/kpi/evaluation-export.md,
// the KPIs per arm and scenario, the difference from the baseline and the DSC targets.

export type Arm = "BL-0" | "BL-1" | "BL-2" | "BL-3" | "BL-5" | "DSS";

export interface EvalRow {
  scenario: string;
  title?: string;
  arm: Arm;
  kpis: Record<string, number | null>;
  plans_verified?: { verified: number; built: number } | null;
  notes?: string;
}

export interface EvalRun {
  network_version: string;
  register_version: string;
  scenario_set: string;
  seed: number;
  date?: string;
  window?: { from_h: number; to_h: number };
  generated_by?: string;
  generated_on?: string;
  sample?: boolean;
  notes?: string;
}

export interface EvalDoc {
  schema_version: 1;
  run: EvalRun;
  rows: EvalRow[];
}

/**
 * The scope of a table: one scenario, or `ALL`: the mean over the scenarios that have a row for every loop arm of
 * the export (see `commonScenarios`), so that the arms are compared on the same scenarios. BL-5 (history) keeps
 * its own rows.
 */
export const ALL = "__all__";

export const ARM_ORDER: Arm[] = ["BL-0", "BL-1", "BL-2", "BL-3", "BL-5"];

export const ARM_LABELS: Record<Arm, string> = {
  "BL-0": "BL-0 no action",
  "BL-1": "BL-1 greedy",
  "BL-2": "BL-2 DSS",
  "BL-3": "BL-3 noisy DSS",
  "BL-5": "BL-5 history",
  DSS: "BL-2 DSS",
};

/** `DSS` is the name the loop reports use for BL-2: the view treats them alike. */
export function arm(a: Arm): Arm {
  return a === "DSS" ? "BL-2" : a;
}

export type Direction = "lower" | "higher";

export interface Target {
  op: "<=" | ">=" | "==";
  value: number;
  dsc: string;
}

export interface KpiDef {
  key: string;
  label: string;
  unit: string;
  better: Direction | null; // null: no direction (a count of trains that left, say)
  target?: Target; // an absolute target (DSC-04, 05, 09); the others are judged against a baseline
}

export const KPI_DEFS: KpiDef[] = [
  { key: "A5_weighted_delay_train_min", label: "Weighted delay", unit: "train-min", better: "lower" },
  { key: "A2_section_punctuality_pct", label: "Section punctuality", unit: "%", better: "higher" },
  { key: "A2_lop_count", label: "Trains late at exit", unit: "", better: "lower" },
  { key: "A4_mean_arrival_delay_min", label: "Mean arrival delay", unit: "min", better: "lower" },
  { key: "A4_max_arrival_delay_min", label: "Longest arrival delay", unit: "min", better: "lower" },
  { key: "A6_mean_exit_delay_min", label: "Mean exit delay", unit: "min", better: "lower" },
  { key: "A6_median_exit_delay_min", label: "Median exit delay", unit: "min", better: "lower" },
  { key: "A6_p90_exit_delay_min", label: "P90 exit delay", unit: "min", better: "lower" },
  { key: "A9_mean_time_lost_min", label: "Mean time lost", unit: "min", better: "lower" },
  { key: "B1_trains_exited", label: "Trains exited", unit: "", better: null },
  { key: "C1_freight_speed_kmh", label: "Freight speed", unit: "km/h", better: "higher" },
  { key: "C2_freight_detention_h", label: "Freight detention", unit: "h", better: "lower" },
  { key: "F1_unplanned_stops", label: "Unplanned stops", unit: "", better: "lower" },
  {
    key: "D1_median_conflict_lead_min",
    label: "Median conflict lead time",
    unit: "min",
    better: "higher",
    target: { op: ">=", value: 15, dsc: "DSC-05" },
  },
  {
    key: "D2_resolved_before_occurrence_pct",
    label: "Resolved before occurrence",
    unit: "%",
    better: "higher",
    target: { op: ">=", value: 90, dsc: "DSC-04" },
  },
  {
    key: "H1_alerts_per_controller_hour",
    label: "Alerts per controller-hour",
    unit: "",
    better: "lower",
    target: { op: "<=", value: 6, dsc: "DSC-09" },
  },
  {
    key: "H2_flood_events",
    label: "Flood events",
    unit: "",
    better: "lower",
    target: { op: "==", value: 0, dsc: "DSC-09" },
  },
];

export function isSample(doc: EvalDoc | null): boolean {
  return !!doc && doc.run.sample === true;
}

/** The banner of a sample export: its own note if that says so, else the standard sentence plus the note. */
export function sampleBanner(doc: EvalDoc): string {
  const note = doc.run.notes ?? "";
  if (note.startsWith("SAMPLE DATA")) return note;
  return `SAMPLE DATA: invented numbers that exercise the view, not results.${note ? ` ${note}` : ""}`;
}

export function runLine(doc: EvalDoc): string {
  const r = doc.run;
  const when = r.date ? ` · ${r.date}` : r.generated_on ? ` · run ${r.generated_on}` : "";
  const window = r.window ? ` · window ${r.window.from_h}–${r.window.to_h} h` : "";
  return `${r.scenario_set} · network ${r.network_version} · register ${r.register_version} · seed ${r.seed}${when}${window}`;
}

export interface ScenarioRef {
  id: string;
  title: string;
}

/** The scenarios of the export in the order they first appear, with the first title given. */
export function scenarios(doc: EvalDoc): ScenarioRef[] {
  const seen = new Map<string, string>();
  for (const r of doc.rows) {
    if (!seen.has(r.scenario)) seen.set(r.scenario, r.title ?? "");
    else if (!seen.get(r.scenario) && r.title) seen.set(r.scenario, r.title);
  }
  return [...seen].map(([id, title]) => ({ id, title }));
}

/** The scenarios with a row for every arm of the export but BL-5 (all scenarios if there are none). */
export function commonScenarios(doc: EvalDoc): string[] {
  const loopArms = new Set(doc.rows.map((r) => arm(r.arm)).filter((a) => a !== "BL-5"));
  const ids = scenarios(doc).map((s) => s.id);
  const full = ids.filter((id) => [...loopArms].every((a) => doc.rows.some((r) => r.scenario === id && arm(r.arm) === a)));
  return full.length > 0 ? full : ids;
}

function rowsOf(doc: EvalDoc, scope: string, a?: Arm): EvalRow[] {
  if (scope !== ALL) return doc.rows.filter((r) => r.scenario === scope);
  if (a === "BL-5") return doc.rows;
  const ids = new Set(commonScenarios(doc));
  return doc.rows.filter((r) => ids.has(r.scenario));
}

/** The arms present in a scope, in the fixed order (DSS counts as BL-2). */
export function armsIn(doc: EvalDoc, scope: string): Arm[] {
  const present = new Set((scope === ALL ? doc.rows : rowsOf(doc, scope)).map((r) => arm(r.arm)));
  return ARM_ORDER.filter((a) => present.has(a));
}

/** The KPIs the export has (any row), the known ones first in their fixed order, then unknown names. */
export function kpisPresent(doc: EvalDoc): KpiDef[] {
  const keys = new Set(doc.rows.flatMap((r) => Object.keys(r.kpis)));
  const known = KPI_DEFS.filter((d) => keys.has(d.key));
  const unknown = [...keys]
    .filter((k) => !KPI_DEFS.some((d) => d.key === k))
    .sort()
    .map((key): KpiDef => ({ key, label: key, unit: "", better: null }));
  return [...known, ...unknown];
}

export interface Value {
  value: number | null;
  n: number; // scenarios the value is from (1 for one scenario)
}

/** One KPI of one arm: the value of the scenario, or the mean over the scenarios that have one. */
export function valueOf(doc: EvalDoc, scope: string, a: Arm, key: string): Value {
  const vals = rowsOf(doc, scope, a)
    .filter((r) => arm(r.arm) === a)
    .map((r) => r.kpis[key])
    .filter((v): v is number => typeof v === "number");
  if (vals.length === 0) return { value: null, n: 0 };
  return { value: vals.reduce((s, v) => s + v, 0) / vals.length, n: vals.length };
}

export type Verdict = "better" | "worse" | "same";

export function verdict(better: Direction | null, delta: number): Verdict | null {
  if (better === null) return null;
  if (Math.abs(delta) < 0.005) return "same";
  return (delta < 0) === (better === "lower") ? "better" : "worse";
}

export type TargetState = "met" | "missed";

export function targetState(t: Target | undefined, v: number | null): TargetState | null {
  if (!t || v === null) return null;
  const ok = t.op === ">=" ? v >= t.value : t.op === "<=" ? v <= t.value : v === t.value;
  return ok ? "met" : "missed";
}

export interface Cell {
  value: number | null;
  delta: number | null; // against BL-0 (null for BL-0 itself or when either side has no value)
  verdict: Verdict | null;
  target: TargetState | null;
}

const round2 = (x: number) => Math.round(x * 100) / 100;

/** A cell of the table: the arm's value, its difference from BL-0 and its state against the absolute target. */
export function cell(doc: EvalDoc, scope: string, a: Arm, def: KpiDef): Cell {
  const v = valueOf(doc, scope, a, def.key).value;
  const base = valueOf(doc, scope, "BL-0", def.key).value;
  // BL-5 is a different measure (the historical log): no difference from BL-0
  const delta = a === "BL-0" || a === "BL-5" || v === null || base === null ? null : round2(v - base);
  return {
    value: v === null ? null : round2(v),
    delta,
    verdict: delta === null ? null : verdict(def.better, delta),
    target: a === "BL-0" || a === "BL-5" ? null : targetState(def.target, v),
  };
}

/** "70 of 74" for one scenario, the sum over scenarios for all; null for an arm without a loop. */
export function plansVerified(doc: EvalDoc, scope: string, a: Arm): string | null {
  const rows = rowsOf(doc, scope, a).filter((r) => arm(r.arm) === a && r.plans_verified);
  if (rows.length === 0) return null;
  const verified = rows.reduce((s, r) => s + r.plans_verified!.verified, 0);
  const built = rows.reduce((s, r) => s + r.plans_verified!.built, 0);
  return `${verified} of ${built}`;
}

export interface Check {
  dsc: string;
  text: string;
  value: string;
  met: boolean | null; // null: the export does not have what the check needs
}

const pct = (x: number) => `${round2(x)} %`;

/** The M2 acceptance criteria that can be read from the export, for the DSS arm (BL-2) in a scope. */
export function dscChecks(doc: EvalDoc, scope: string): Check[] {
  const v = (a: Arm, key: string) => valueOf(doc, scope, a, key).value;
  const out: Check[] = [];
  const a5 = { dss: v("BL-2", "A5_weighted_delay_train_min"), bl1: v("BL-1", "A5_weighted_delay_train_min"), bl0: v("BL-0", "A5_weighted_delay_train_min") };
  const cut = (x: number | null, base: number | null) => (x === null || base === null || base === 0 ? null : (100 * (base - x)) / base);
  const vs1 = cut(a5.dss, a5.bl1);
  const vs0 = cut(a5.dss, a5.bl0);
  out.push({ dsc: "DSC-01", text: "Weighted delay at least 15 % below BL-1", value: vs1 === null ? "-" : `${pct(vs1)} below`, met: vs1 === null ? null : vs1 >= 15 });
  out.push({ dsc: "DSC-01", text: "and at least 25 % below BL-0", value: vs0 === null ? "-" : `${pct(vs0)} below`, met: vs0 === null ? null : vs0 >= 25 });
  const p2 = v("BL-2", "A2_section_punctuality_pct");
  const p1 = v("BL-1", "A2_section_punctuality_pct");
  const gain = p2 === null || p1 === null ? null : p2 - p1;
  out.push({ dsc: "DSC-02", text: "Section punctuality at least 5 points above BL-1", value: gain === null ? "-" : `${gain >= 0 ? "+" : ""}${round2(gain)} points`, met: gain === null ? null : gain >= 5 });
  const c2 = v("BL-2", "C2_freight_detention_h");
  const c1 = v("BL-1", "C2_freight_detention_h");
  const worse = c2 === null || c1 === null || c1 === 0 ? null : (100 * (c2 - c1)) / c1;
  out.push({ dsc: "DSC-03", text: "Freight detention not worse than BL-1 by more than 5 %", value: worse === null ? "-" : `${worse >= 0 ? "+" : ""}${pct(worse)}`, met: worse === null ? null : worse <= 5 });
  for (const def of KPI_DEFS.filter((d) => d.target)) {
    const t = def.target!;
    const x = v("BL-2", def.key);
    out.push({ dsc: t.dsc, text: `${def.label} ${t.op === "==" ? "none" : t.op === ">=" ? `at least ${t.value}` : `at most ${t.value}`}${def.unit ? ` ${def.unit}` : ""}`, value: x === null ? "-" : `${round2(x)}`, met: targetState(t, x) === null ? null : targetState(t, x) === "met" });
  }
  return out;
}
