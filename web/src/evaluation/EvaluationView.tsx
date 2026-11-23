// Management KPI view (issue #173): the KPIs per arm (BL-0 / BL-1 / BL-2 / BL-3 / BL-5) and per scenario from the
// evaluation export (docs/kpi/evaluation-export.md), the difference from BL-0 and the DSC targets. It shows a
// sample-data banner while the export is marked `sample`.

import { useState } from "react";
import {
  ALL,
  ARM_LABELS,
  type EvalDoc,
  armsIn,
  cell,
  commonScenarios,
  dscChecks,
  isSample,
  kpisPresent,
  plansVerified,
  runLine,
  sampleBanner,
  scenarios,
} from "./evaluation";

interface Props {
  doc: EvalDoc | null;
  error: string;
  onClose: () => void;
}

const fmt = (x: number) => x.toLocaleString("en", { maximumFractionDigits: 2 });

export function EvaluationView({ doc, error, onClose }: Props) {
  const [scope, setScope] = useState<string>(ALL);
  const refs = doc ? scenarios(doc) : [];
  const arms = doc ? armsIn(doc, scope) : [];
  const defs = doc ? kpisPresent(doc) : [];
  const checks = doc ? dscChecks(doc, scope) : [];
  const notes = doc ? doc.rows.filter((r) => r.notes && (scope === ALL || r.scenario === scope)) : [];
  return (
    <div className="evalview" role="dialog" aria-label="Management KPIs">
      <div className="about-head">
        <h3>Management KPIs by arm</h3>
        <button onClick={onClose} aria-label="Close management view">
          ✕
        </button>
      </div>
      {isSample(doc) && (
        <p className="banner banner-sample" role="note">
          {doc ? sampleBanner(doc) : ""}
        </p>
      )}
      {error && <p className="muted">{error}</p>}
      {doc && (
        <>
          <p className="muted">{runLine(doc)}</p>
          <label className="eval-scope">
            Scenario{" "}
            <select value={scope} onChange={(e) => setScope(e.target.value)} aria-label="Scenario">
              <option value={ALL}>All scenarios (mean)</option>
              {refs.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.id}
                  {s.title ? `: ${s.title}` : ""}
                </option>
              ))}
            </select>
          </label>

          <p className="muted">
            {scope === ALL
              ? `Mean over ${commonScenarios(doc).join(", ")} (the scenarios with every arm); choose a scenario for the others. BL-5 is the historical log: its own rows, no difference from BL-0.`
              : `Scenario ${scope}.`}
          </p>
          <h4>KPIs</h4>
          <table className="eval-table">
            <thead>
              <tr>
                <th>KPI</th>
                {arms.map((a) => (
                  <th key={a}>{ARM_LABELS[a]}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {defs.map((def) => (
                <tr key={def.key}>
                  <td title={def.key}>
                    {def.label}
                    {def.unit ? ` (${def.unit})` : ""}
                    {def.target ? <span className="muted"> · {def.target.dsc} {def.target.op} {def.target.value}</span> : null}
                  </td>
                  {arms.map((a) => {
                    const c = cell(doc, scope, a, def);
                    return (
                      <td key={a} className={c.verdict ? `verdict-${c.verdict}` : ""}>
                        {c.value === null ? <span className="muted">-</span> : fmt(c.value)}
                        {c.delta !== null && (
                          <span className="eval-delta">
                            {" "}
                            ({c.delta >= 0 ? "+" : ""}
                            {fmt(c.delta)})
                          </span>
                        )}
                        {c.target && (
                          <span className={c.target === "met" ? "pass" : "fail"} title={c.target === "met" ? "target met" : "target missed"}>
                            {" "}
                            {c.target === "met" ? "✓" : "✗"}
                          </span>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr>
                <td>Plans verified (G0-G3)</td>
                {arms.map((a) => (
                  <td key={a}>{plansVerified(doc, scope, a) ?? <span className="muted">-</span>}</td>
                ))}
              </tr>
            </tbody>
          </table>
          <p className="muted">
            In brackets: the difference from BL-0 (green better, red worse). ✓ / ✗: the absolute target of the KPI.
          </p>

          <h4>Acceptance criteria for the DSS arm (BL-2)</h4>
          <table className="eval-table">
            <tbody>
              {checks.map((c) => (
                <tr key={`${c.dsc}-${c.text}`}>
                  <td>{c.dsc}</td>
                  <td>{c.text}</td>
                  <td>{c.value}</td>
                  <td className={c.met === null ? "muted" : c.met ? "pass" : "fail"}>{c.met === null ? "n/a" : c.met ? "met" : "not met"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {notes.length > 0 && (
            <>
              <h4>Notes</h4>
              <ul className="whatif-orders">
                {notes.map((r) => (
                  <li key={`${r.scenario}-${r.arm}`}>
                    {r.scenario} {ARM_LABELS[r.arm]}: {r.notes}
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </div>
  );
}
