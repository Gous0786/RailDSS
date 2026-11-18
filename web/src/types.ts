// Control-chart data exported by `python -m raildss_twin chart` (raildss_twin.chart, issue #86).
// Times are seconds since the start of the day; distances are km from KTV.

export type Point = [number, number]; // [t_s, km]

export interface Station {
  code: string;
  name: string;
  km: number;
  halt: boolean;
  crossing: boolean;
  lines: number;
  loops: number;
  max_loop_m: number | null;
}

export interface Train {
  id: string;
  name: string;
  kind: "passenger" | "freight" | string;
  category: string;
  class: string; // policy class (OD-04): premium, mail_express, passenger, freight_other
  weight: number;
  load: { wagons: number; tonnes: number } | null; // freight only
  entry_delay_s: number | null; // timetabled trains
  exit_delay_s: number | null;
  actual: Point[];
  booked: Point[];
}

export type Priority = "P1" | "P2" | "P3" | "P4";

/** FR-UI-08 alert record. */
export interface Alert {
  id: string;
  priority: Priority;
  type: string; // delay | held
  trains: string[];
  location: string;
  km: number;
  time_s: number;
  lead_min: number;
  recommendation: string | null;
  state: string;
  raised_by: string;
  detail: string;
}

export interface Impact {
  id: string;
  name: string;
  class: string;
  weight: number;
  delta_s: number;
  exit_before_s: number;
  exit_after_s: number;
}

export interface Verification {
  status: "PASS" | "FAIL";
  gate: string;
  violations: number;
  warnings: number;
  rules: { rule_id: string; status: string; violations: number }[];
}

export interface Explanation {
  why: string;
  why_not: string;
  weights: Record<string, number>;
  hard_rules: { rule: string; applied: boolean; note: string }[];
}

export interface Option {
  id: string;
  title: string;
  orders: string[];
  trains: Record<string, Point[]>; // proposed lines of the trains the option changes
  impacts: Impact[]; // per changed train against the baseline, most affected first
  weighted_delay_delta_min: number;
  verification: Verification;
  explanation: Explanation;
  confidence: number | null;
  assumptions: string[];
}

export interface Preview {
  id: string;
  demo: boolean;
  context: string;
  options: Option[];
  valid_until_s: number; // the card expires at this time
}

export interface ChartData {
  format: string;
  version: number;
  network_version: string;
  day: string;
  scenario_id: string;
  trace_hash: string;
  completed: boolean;
  ui: { delay_badge_thresholds_min: Record<string, number>; palette_coa: Record<string, string> };
  policy: { weights: Record<string, number>; hard_rules: string[]; weights_in_explanations: boolean };
  alerts: Alert[];
  stations: Station[];
  trains: Train[];
  preview: Preview;
}
