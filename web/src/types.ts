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
  actual: Point[];
  booked: Point[];
}

export interface Option {
  id: string;
  title: string;
  orders: string[];
  trains: Record<string, Point[]>; // proposed lines of the trains the option changes
}

export interface Preview {
  id: string;
  demo: boolean;
  context: string;
  options: Option[];
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
  stations: Station[];
  trains: Train[];
  preview: Preview;
}
