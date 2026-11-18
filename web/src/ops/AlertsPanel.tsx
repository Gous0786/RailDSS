// Alerts and conflicts panel (FR-UI-08): grouped alerts, one-action acknowledge, click to focus the chart.

import type { Alert, Priority } from "../types";
import { hhmm } from "../chart/geometry";
import { type AlertGroup, leadMin } from "./alerts";

interface Props {
  groups: AlertGroup[];
  now: number;
  acked: ReadonlySet<string>;
  cursor: number; // index of the focused group
  minPriority: Priority;
  onMinPriority: (p: Priority) => void;
  onFocus: (a: Alert) => void;
  onAck: (g: AlertGroup) => void;
  onCursor: (i: number) => void;
}

export function AlertsPanel({ groups, now, acked, cursor, minPriority, onMinPriority, onFocus, onAck, onCursor }: Props) {
  return (
    <section className="alerts-panel" aria-label="Alerts and conflicts">
      <h3>
        Alerts{" "}
        <select value={minPriority} onChange={(e) => onMinPriority(e.target.value as Priority)} aria-label="Lowest priority shown">
          <option value="P1">P1</option>
          <option value="P2">≥ P2</option>
          <option value="P3">≥ P3</option>
        </select>
      </h3>
      <p className="muted keys">
        <kbd>J</kbd>/<kbd>K</kbd> move · <kbd>Enter</kbd> focus on the chart · <kbd>X</kbd> acknowledge
      </p>
      {groups.length === 0 && <p className="muted">No open alerts.</p>}
      <ul className="alert-list">
        {groups.slice(0, 40).map((g, i) => {
          const a = g.parent;
          const done = g.open === 0;
          return (
            <li key={a.id} className={`alert ${a.priority} ${i === cursor ? "cur" : ""} ${done ? "acked" : ""}`}>
              <button className="alert-main" onClick={() => (onCursor(i), onFocus(a))} aria-label={`${a.priority} ${a.detail}`}>
                <b className="pri">{a.priority}</b> <span>{a.detail}</span>
                <span className="muted">
                  {" "}
                  · {hhmm(a.time_s)} · {a.type}
                  {g.children.length > 0 ? ` · +${g.children.length} related` : ""}
                  {a.time_s > now ? ` · in ${Math.round(leadMin(a, now))} min` : ""}
                </span>
              </button>
              <button className="ack" disabled={done} onClick={() => onAck(g)} aria-label={`Acknowledge ${a.id}`}>
                {done || [a, ...g.children].every((x) => acked.has(x.id)) ? "✓" : "Ack"}
              </button>
            </li>
          );
        })}
      </ul>
      {groups.length > 40 && <p className="muted">… {groups.length - 40} more (raise the priority filter).</p>}
    </section>
  );
}
