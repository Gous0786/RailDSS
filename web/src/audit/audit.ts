// Audit and replay view logic (FR-UI-13, FR-AUD-05; issue #85): the exported log, filters and the timeline.

export interface AuditRecord {
  seq: number;
  section: string;
  day: string;
  time: string; // ISO, UTC
  kind: string;
  actor: string | null;
  refs: { trains?: string[]; rec_id?: string; incident?: string; run_id?: string; order_id?: string };
  payload: Record<string, unknown>;
  prev: string;
  hash: string;
}

export interface AuditDoc {
  section: string;
  verification: { ok: boolean; records: number; days: number; problems: string[] };
  records: AuditRecord[];
}

export interface AuditFilter {
  kind: string; // "" = every kind
  text: string; // train, recommendation, incident, order or controller (pseudonym)
}

export const NO_AUDIT_FILTER: AuditFilter = { kind: "", text: "" };
const IST_OFFSET_S = 5.5 * 3600;
const DAY_S = 24 * 3600;

/** Seconds since midnight IST: the time axis of the control chart. */
export function chartTime(rec: Pick<AuditRecord, "time">): number {
  const utcS = Date.parse(rec.time) / 1000;
  return (((utcS + IST_OFFSET_S) % DAY_S) + DAY_S) % DAY_S;
}

export function matches(rec: AuditRecord, f: AuditFilter): boolean {
  if (f.kind && rec.kind !== f.kind) return false;
  const q = f.text.trim().toLowerCase();
  if (!q) return true;
  const hay = [...(rec.refs.trains ?? []), rec.refs.rec_id, rec.refs.incident, rec.refs.order_id, rec.actor];
  return hay.some((h) => (h ?? "").toLowerCase().includes(q));
}

/** The log as it stood at ``t`` (chart seconds), filtered, in chain order. */
export function visible(records: readonly AuditRecord[], f: AuditFilter, t: number): AuditRecord[] {
  return records.filter((r) => chartTime(r) <= t && matches(r, f));
}

export function kinds(records: readonly AuditRecord[]): string[] {
  return [...new Set(records.map((r) => r.kind))].sort();
}

export function span(records: readonly AuditRecord[]): [number, number] {
  if (records.length === 0) return [0, 0];
  const ts = records.map(chartTime);
  return [Math.min(...ts), Math.max(...ts)];
}

/** One line per record. */
export function summary(rec: AuditRecord): string {
  const p = rec.payload as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  switch (rec.kind) {
    case "alert":
      return `${p.priority} ${p.detail}`;
    case "plan_run":
      return `${p.run_id}: ${(p.candidates as unknown[]).length} candidate plans, state ${String(p.state?.hash ?? "").slice(0, 8)}`;
    case "recommendation":
      return `${p.rec_id} option ${p.rank} (${p.candidate}): ${(p.explanation as string[]).join("; ")}`;
    case "decision":
      return `${p.rec_id} ${p.action} by ${rec.actor}: ${p.reason ?? ""} (${p.time_to_decision_s ?? "-"} s)`;
    case "order":
      return `${p.order_id}: ${p.text}`;
    case "ack":
      return `${p.order_id} ${p.status}${p.reason_code ? ` (${p.reason_code})` : ""}`;
    case "outcome":
      return `${p.rec_id} → ${p.status}`;
    case "config_change":
      return `${p.item} ${p.key}: ${JSON.stringify(p.before)} → ${JSON.stringify(p.after)}`;
    default:
      return rec.kind;
  }
}

/** What the recommendation card showed at the time: the explanation lines, every option and the basis. */
export function cardOf(rec: AuditRecord) {
  const p = rec.payload as Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
  return {
    id: String(p.rec_id),
    lines: (p.explanation ?? []) as string[],
    options: (p.options ?? []) as { rec_id: string; rank: number; candidate: string; explanation: string[] }[],
    stateHash: String(p.state?.hash ?? ""),
    versions: (p.versions ?? {}) as Record<string, string>,
    seed: Number(p.seed ?? 0),
    verification: (p.verification ?? {}) as { fail: string[]; warn: string[]; g3: string },
  };
}
