// Train queue, next 2 h (FR-UI-07): sortable and filterable.

import { hhmm } from "../chart/geometry";
import { type QueueFilter, type QueueRow, type SortKey } from "./queue";

const COLUMNS: { key: SortKey | null; label: string }[] = [
  { key: "train", label: "Train" },
  { key: "class", label: "Class" },
  { key: "dir", label: "Dir" },
  { key: null, label: "Location" },
  { key: null, label: "Next point" },
  { key: null, label: "Sched → ETA" },
  { key: "delay", label: "Delay" },
  { key: null, label: "Load" },
  { key: null, label: "Crew" },
  { key: null, label: "Flags" },
  { key: null, label: "Next action" },
];

interface Props {
  rows: QueueRow[];
  total: number;
  sort: SortKey;
  desc: boolean;
  filter: QueueFilter;
  classes: string[];
  selected: string | null;
  onSort: (k: SortKey) => void;
  onFilter: (f: QueueFilter) => void;
  onSelect: (id: string) => void;
}

export function QueuePanel({ rows, total, sort, desc, filter, classes, selected, onSort, onFilter, onSelect }: Props) {
  return (
    <section className="queue" aria-label="Train queue, next 2 hours">
      <div className="queue-head">
        <h3>
          Train queue · next 2 h{" "}
          <span className="muted">
            ({rows.length}
            {rows.length !== total ? ` of ${total}` : ""} trains · sorted by {sort === "eta" ? "section entry, then priority" : sort}
            {desc ? " ↓" : ""})
          </span>
        </h3>
        <input
          type="search"
          placeholder="train no. or name"
          value={filter.text}
          onChange={(e) => onFilter({ ...filter, text: e.target.value })}
          aria-label="Filter by train number or name"
        />
        <select value={filter.cls} onChange={(e) => onFilter({ ...filter, cls: e.target.value })} aria-label="Filter by class">
          <option value="">all classes</option>
          {classes.map((c) => (
            <option key={c}>{c}</option>
          ))}
        </select>
        <select value={filter.dir} onChange={(e) => onFilter({ ...filter, dir: e.target.value as QueueFilter["dir"] })} aria-label="Filter by direction">
          <option value="">both directions</option>
          <option value="DN">DN</option>
          <option value="UP">UP</option>
        </select>
        <label>
          <input type="checkbox" checked={filter.flaggedOnly} onChange={(e) => onFilter({ ...filter, flaggedOnly: e.target.checked })} /> flagged
        </label>
        <span className="muted keys">
          <kbd>S</kbd> sort · <kbd>F</kbd> flagged
        </span>
      </div>
      <div className="queue-scroll">
        <table>
          <thead>
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.label}>
                  {c.key ? (
                    <button onClick={() => onSort(c.key as SortKey)} aria-label={`Sort by ${c.label}`}>
                      {c.label}
                      {sort === c.key ? (desc ? " ↓" : " ↑") : ""}
                    </button>
                  ) : (
                    c.label
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={r.id === selected ? "sel-row" : ""} onClick={() => onSelect(r.id)}>
                <td title={r.name}>{r.id.length > 22 ? `${r.id.slice(0, 20)}…` : r.id}</td>
                <td>{r.class.replace("_", " ")}</td>
                <td>{r.dir}</td>
                <td>{r.location}</td>
                <td>{r.nextPoint}</td>
                <td>{r.scheduled === null ? `ETA ${hhmm(r.eta)}` : `${hhmm(r.scheduled)} → ${hhmm(r.eta)}`}</td>
                <td className={r.delayMin !== null && r.delayMin >= 15 ? "late" : ""}>{r.delayMin === null ? "—" : `${r.delayMin >= 0 ? "+" : "−"}${Math.abs(r.delayMin).toFixed(0)} min`}</td>
                <td>{r.load}</td>
                <td className="muted">{r.crew}</td>
                <td>{r.flags.map((f) => <span key={f} className={`flag flag-${f}`}>{f}</span>)}</td>
                <td>{r.nextAction}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {rows.length === 0 && <p className="muted">No trains match.</p>}
      </div>
    </section>
  );
}
