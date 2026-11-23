// Disruption and what-if view (FR-UI-11; issue #172): the incident list and detail, the DSS variants of a
// scenario export drawn on the chart against the baseline, and the plan comparison table. Always a sandbox: it
// reads an export of twin replays and never changes anything live.

import { useState } from "react";
import type { PreviewMode } from "../chart/ControlChart";
import { hhmm } from "../chart/geometry";
import type { ChartData } from "../types";
import { comparisonTable, etrLabel, etrSlipMin, impactSummary, label, phaseOf } from "./whatif";

interface Props {
  data: ChartData;
  variant: number; // index into preview.options, or -1: the baseline alone
  onVariant: (i: number) => void;
  mode: PreviewMode;
  onMode: (m: PreviewMode) => void;
  now: number;
  onClose: () => void;
}

const MODES: [PreviewMode, string][] = [
  ["before", "baseline"],
  ["after", "variant"],
  ["off", "variant off"],
];

export function WhatIfView({ data, variant, onVariant, mode, onMode, now, onClose }: Props) {
  const [open, setOpen] = useState<string | null>(data.disruptions?.[0]?.id ?? null);
  const disruptions = data.disruptions ?? [];
  const options = data.preview.options;
  const shown = disruptions.find((d) => d.id === open);
  const slip = shown ? etrSlipMin(shown) : null;
  const table = comparisonTable(data.whatif?.baseline.kpis ?? {}, options);
  const chosen = variant >= 0 ? options[variant] : undefined;
  const impact = chosen ? impactSummary(chosen) : null;
  return (
    <div className="whatif" role="dialog" aria-label="Disruption and what-if">
      <div className="about-head">
        <h3>
          Disruption &amp; what-if <span className="banner banner-sandbox">✎ SANDBOX</span>
        </h3>
        <button onClick={onClose} aria-label="Close what-if view">
          ✕
        </button>
      </div>
      <p className="muted">
        {data.scenario ? `${data.scenario.id}: ${data.scenario.title}. ` : ""}Replays of the twin: nothing here changes the
        live state, and no order is sent.
      </p>

      <h4>Incidents</h4>
      <ul className="whatif-list">
        {disruptions.map((d) => (
          <li key={d.id} className={open === d.id ? "sel" : ""}>
            <button onClick={() => setOpen(d.id)}>
              <b>{label(d)}</b> · {hhmm(d.start_s)}–{hhmm(d.end_s)} ·{" "}
              <span className={`phase phase-${phaseOf(d, now)}`}>{phaseOf(d, now)}</span>
            </button>
          </li>
        ))}
      </ul>
      {shown && (
        <table className="whatif-detail">
          <tbody>
            <tr>
              <td>Type / cause</td>
              <td>
                {shown.type}
                {shown.cause_code ? ` · ${shown.cause_code}` : ""}
              </td>
            </tr>
            <tr>
              <td>Location</td>
              <td>
                {shown.location}
                {shown.km_from !== null ? ` (km ${shown.km_from}–${shown.km_to})` : ""}
              </td>
            </tr>
            <tr>
              <td>Start / real end</td>
              <td>
                {hhmm(shown.start_s)} / {hhmm(shown.end_s)}
              </td>
            </tr>
            <tr>
              <td>ETR</td>
              <td>
                {etrLabel(shown)}
                {slip !== null ? ` · the real end is ${slip >= 0 ? "+" : ""}${slip} min against the ETR` : ""}
              </td>
            </tr>
            <tr>
              <td>Lines affected</td>
              <td>
                {shown.lines_affected.join(", ") || "-"}
                {shown.single_line_working ? " · single-line working" : ""}
              </td>
            </tr>
            <tr>
              <td>Linked trains</td>
              <td>{shown.linked_trains.join(", ") || "-"}</td>
            </tr>
            <tr>
              <td>Status / owner</td>
              <td>
                {shown.status} · {shown.owner}
              </td>
            </tr>
            {shown.note && (
              <tr>
                <td>Note</td>
                <td>{shown.note}</td>
              </tr>
            )}
          </tbody>
        </table>
      )}

      <h4>Variants on the chart</h4>
      <div className="whatif-pick" role="radiogroup" aria-label="Variant drawn on the chart">
        <label>
          <input type="radio" name="variant" checked={variant < 0} onChange={() => onVariant(-1)} /> Baseline (BL-0)
        </label>
        {options.map((o, i) => (
          <label key={o.id}>
            <input type="radio" name="variant" checked={variant === i} onChange={() => onVariant(i)} /> {o.title}
          </label>
        ))}
        <span className="whatif-mode">
          Chart:{" "}
          {MODES.map(([m, text]) => (
            <button key={m} className={mode === m ? "on" : ""} onClick={() => onMode(m)} disabled={variant < 0 && m !== "off"}>
              {text}
            </button>
          ))}
        </span>
      </div>
      {chosen && impact && (
        <div className="whatif-chosen">
          <p>
            Verifier: <b className={chosen.verification.status === "PASS" ? "pass" : "fail"}>{chosen.verification.status}</b> (
            {chosen.verification.violations} violations, {chosen.verification.warnings} warnings) · {impact.later} trains later,{" "}
            {impact.earlier} earlier
            {impact.worst ? ` · worst ${impact.worst}` : ""}
            {chosen.loop
              ? ` · loop ${chosen.loop.window_h[0]}–${chosen.loop.window_h[1]} h, ${chosen.loop.plans} plans (${chosen.loop.candidates_verified} verified)`
              : ""}
          </p>
          <p className="muted">
            Orders ({chosen.orders_total ?? chosen.orders.length}
            {chosen.orders_total && chosen.orders_total > chosen.orders.length ? `, first ${chosen.orders.length}` : ""}):
          </p>
          <ul className="whatif-orders">
            {chosen.orders.map((o) => (
              <li key={o}>{o}</li>
            ))}
          </ul>
        </div>
      )}

      <h4>Plan comparison</h4>
      <table className="whatif-table">
        <thead>
          <tr>
            <th>KPI</th>
            <th>Baseline</th>
            {options.map((o) => (
              <th key={o.id}>{o.arm ?? o.id}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.map(({ row, baseline, variants }) => (
            <tr key={row.key}>
              <td>{row.label}</td>
              <td>{baseline.toLocaleString("en", { maximumFractionDigits: 2 })}</td>
              {variants.map((c, i) => (
                <td key={options[i].id} className={`verdict-${c.verdict}`}>
                  {c.value.toLocaleString("en", { maximumFractionDigits: 2 })} ({c.delta >= 0 ? "+" : ""}
                  {c.delta})
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">Green: better than the baseline; red: worse. The mimic shows the baseline run.</p>
    </div>
  );
}
