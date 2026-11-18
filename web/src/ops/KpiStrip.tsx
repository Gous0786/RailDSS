// KPI strip (FR-UI-10): value, 4 h sparkline and delta per KPI; neutral unless a threshold is breached.

import type { Kpi } from "./kpi";

function Spark({ values }: { values: (number | null)[] }) {
  const pts = values.map((v, i) => [i, v] as const).filter((p): p is readonly [number, number] => p[1] !== null);
  if (pts.length < 2) return <svg className="spark" width="64" height="18" aria-hidden="true" />;
  const lo = Math.min(...pts.map((p) => p[1]));
  const hi = Math.max(...pts.map((p) => p[1]));
  const x = (i: number) => (i / (values.length - 1)) * 62 + 1;
  const y = (v: number) => (hi === lo ? 9 : 16 - ((v - lo) / (hi - lo)) * 14);
  return (
    <svg className="spark" width="64" height="18" aria-hidden="true">
      <polyline fill="none" strokeWidth="1.5" points={pts.map(([i, v]) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ")} />
    </svg>
  );
}

function fmt(k: Kpi): string {
  if (k.value === null) return "—";
  return k.id === "PEND" || k.id === "B6" ? String(Math.round(k.value)) : k.value.toFixed(k.id === "A2" || k.id === "D1" ? 0 : 1);
}

export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <div className="kpis" role="group" aria-label="KPI strip">
      {items.map((k) => (
        <div key={k.id} className={`kpi ${k.breach ? "breach" : ""}`} title={`${k.label} (${k.unit})`}>
          <span className="kpi-label">{k.label}</span>
          <span className="kpi-value">
            {fmt(k)} <small>{k.unit}</small>
          </span>
          <span className="kpi-row">
            <Spark values={k.spark} />
            <span className="kpi-delta">{k.delta === null ? "" : `${k.delta >= 0 ? "▲" : "▼"} ${Math.abs(k.delta).toFixed(1)}`}</span>
          </span>
        </div>
      ))}
    </div>
  );
}
