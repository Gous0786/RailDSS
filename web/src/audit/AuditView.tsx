// Audit and replay view (FR-UI-13): filterable event and decision log, timeline scrubbing with the REPLAY
// banner, and the recommendation card as it was shown. Reads the log exported by `python -m raildss_audit export`.

import { useMemo, useState } from "react";
import { hhmm } from "../chart/geometry";
import {
  type AuditDoc,
  type AuditFilter,
  NO_AUDIT_FILTER,
  cardOf,
  chartTime,
  kinds,
  span,
  summary,
  visible,
} from "./audit";

interface Props {
  doc: AuditDoc | null;
  error: string;
  now: number; // chart seconds: the replay position
  onScrub: (t: number) => void;
  onClose: () => void;
}

export function AuditView({ doc, error, now, onScrub, onClose }: Props) {
  const [filter, setFilter] = useState<AuditFilter>(NO_AUDIT_FILTER);
  const [open, setOpen] = useState<number | null>(null);
  const records = doc?.records ?? [];
  const [lo, hi] = useMemo(() => span(records), [records]);
  const rows = useMemo(() => visible(records, filter, now), [records, filter, now]);
  const shown = open !== null ? rows.find((r) => r.seq === open && r.kind === "recommendation") : undefined;
  const card = shown ? cardOf(shown) : null;
  return (
    <div className="audit" role="dialog" aria-label="Audit and replay">
      <div className="about-head">
        <h3>
          Audit &amp; replay <span className="banner banner-replay">⏵ REPLAY {hhmm(now)}</span>
        </h3>
        <button onClick={onClose} aria-label="Close audit view">
          ✕
        </button>
      </div>
      {error && <p className="muted">{error}</p>}
      {doc && (
        <>
          <p className={doc.verification.ok ? "muted" : "audit-bad"}>
            {doc.verification.ok
              ? `Chain intact: ${doc.verification.records} records on ${doc.verification.days} day(s).`
              : `CHAIN BROKEN: ${doc.verification.problems.slice(0, 3).join("; ")}`}
          </p>
          <label className="audit-scrub">
            Timeline {hhmm(lo)}
            <input
              type="range"
              min={lo}
              max={Math.max(hi, lo + 1)}
              step={30}
              value={Math.min(Math.max(now, lo), Math.max(hi, lo + 1))}
              onChange={(e) => onScrub(Number(e.target.value))}
              aria-label="Replay position"
            />
            {hhmm(hi)}
          </label>
          <div className="audit-filter">
            <select value={filter.kind} onChange={(e) => setFilter({ ...filter, kind: e.target.value })} aria-label="Record kind">
              <option value="">all kinds</option>
              {kinds(records).map((k) => (
                <option key={k}>{k}</option>
              ))}
            </select>
            <input
              value={filter.text}
              onChange={(e) => setFilter({ ...filter, text: e.target.value })}
              placeholder="train, REC-0001, INC-…, controller"
              aria-label="Filter by train, recommendation, incident or controller"
            />
            <span className="muted">
              {rows.length} of {records.length}
            </span>
          </div>
          <ul className="audit-list">
            {rows.slice(-60).map((r) => (
              <li key={`${r.day}-${r.seq}`} className={`audit-rec ${r.kind}`}>
                <button onClick={() => setOpen(r.seq === open ? null : r.seq)} aria-label={`${r.kind} ${r.seq}`}>
                  <span className="muted">{hhmm(chartTime(r))}</span> <b>{r.kind}</b> {summary(r)}
                </button>
              </li>
            ))}
          </ul>
          {card && (
            <section className="audit-card" aria-label="Recommendation as shown">
              <h4>{card.id} as shown</h4>
              {card.lines.map((l) => (
                <p key={l}>{l}</p>
              ))}
              <p className="muted">
                Options for this decision: {card.options.map((o) => `${o.rank} ${o.candidate}`).join(", ")} · gate G3{" "}
                {card.verification.g3}
                {card.verification.fail.length ? ` · FAIL ${card.verification.fail.join(", ")}` : ""} · state{" "}
                {card.stateHash.slice(0, 12)} · seed {card.seed} · {Object.entries(card.versions).map(([k, v]) => `${k} ${v}`).join(", ")}
              </p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
