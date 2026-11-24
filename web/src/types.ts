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

/** A disruption of a scenario export (issue #172, FR-UI-11): what ops.disruptions carries, plus the km span. */
export interface Disruption {
  id: string;
  type: string; // line_block | signal_failure | tsr | point_failure | loco_failure | ohe_block | ...
  cause_code: string | null;
  location: string;
  line: string | null;
  km_from: number | null;
  km_to: number | null;
  start_s: number;
  end_s: number; // the real end (ground truth), shown to the analyst, not to the controller
  etr_s: number | null; // the announced ETR
  etr_sigma_min: number | null;
  etr_source: string | null;
  lines_affected: string[];
  linked_trains: string[];
  single_line_working: boolean;
  status: string;
  owner: string;
  note: string;
}

export type KpiSet = Record<string, number>;

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
  // what-if variants (issue #172): the arm, its orders in total, the KPIs against the baseline, the loop's work
  first?: string | null; // contrasting options (issue #174): the train this option serves first at the decision's resource
  arm?: string;
  orders_total?: number;
  kpis?: { baseline: KpiSet; variant: KpiSet };
  loop?: { window_h: [number, number]; plans: number; candidates_verified: string; recommendations_accepted: number; orders_issued: number };
}

/** 'Why not X first?': the loop's re-solve with the other precedence (issue #154, `ContrastRecord`). */
export interface WhyNot {
  first: string; // the train asked about
  resource: string;
  verified: boolean; // the re-solved plan passed the gates
  reversed: boolean; // it really reverses the order of the best plan
  cost_min: number | null; // passenger delay-min against the best plan (positive: it costs more)
  fail: string[]; // the rules that forbid it
  answer: string;
}

export interface Preview {
  id: string;
  demo: boolean;
  context: string;
  options: Option[];
  valid_until_s: number; // the card expires at this time
  // issue #174, optional: a decision of the loop (`DecisionRecord`) and its 'why not' re-solves
  decision?: { trains: string[]; resource: string; options: number; precedences: number };
  why_not?: WhyNot[];
}

// Schematic mimic (FR-UI-06, issue #87): the published layout and the occupancy of the twin run.
export interface MimicStationLine {
  no: number;
  name: string | null;
  kind: "main" | "loop" | "siding" | "yard" | string;
  csr_m: number | null; // usable length
  platform: boolean;
  directions: "DN" | "UP" | "BOTH" | string;
  in_use: boolean;
}

export interface MimicStation {
  code: string;
  name: string;
  km: number;
  halt: boolean;
  lines: MimicStationLine[];
}

export interface MimicBlock {
  id: string;
  segment: string; // occupancy key of the section, e.g. "KPL~KTV"
  from: string;
  to: string;
  from_km: number;
  to_km: number;
  system: string; // AUTO / ABS_...
  lines: { line_id: string; direction: string; sub_blocks: number }[];
}

export interface MimicSignal {
  id: string;
  type: string;
  km: number | null;
  line_id: string | null;
  direction: string;
}

export interface Mimic {
  stations: MimicStation[];
  blocks: MimicBlock[];
  signals: MimicSignal[];
  // labels SL:<op>/<line>, BL:<segment>/<line>, SB:<segment>/<line>#<n>; intervals [resource, train, start, end|null]
  occupancy: { resources: string[]; intervals: [number, number, number, number | null][] };
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
  mimic?: Mimic; // format version 3
  // disruption scenario exports (issue #172)
  scenario?: { id: string; title: string; family: string; seed: number; profile: string; span_h: number };
  disruptions?: Disruption[];
  whatif?: { baseline: { arm: string; kpis: KpiSet }; arms: string[] };
}
