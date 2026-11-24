// Recommendation card and drawer (FR-UI-09, FR-HITL-12/13, OD-04). Keys are handled in App.

import { hhmm } from "../chart/geometry";
import type { ChartData, Option } from "../types";
import { acceptState, countdown, expiresIn, signedMin, topAffected, weightsUsed } from "./card";
import { MAX_OPTIONS, contrastNote, contrastRows, whyNotList, whyNotState, whyNotText } from "./contrast";

type Phase = "none" | "card" | "rejecting" | "done";

const REASONS = [
  "Unsafe / local knowledge (loop or line not usable)",
  "Crew / loco issue",
  "Priority instruction from Dy. CHC / higher authority",
  "Data wrong / ETA disagreement",
  "Other",
];
export { REASONS };

interface Props {
  data: ChartData;
  option: Option | null;
  optionIdx: number;
  phase: Phase;
  now: number;
  preview: string;
  drawer: boolean;
  whyNot?: boolean; // issue #174: the 'why not' answers of the export
}

function Bars({ option, limit }: { option: Option; limit: number }) {
  const rows = option.impacts.slice(0, limit);
  const max = Math.max(1, ...rows.map((r) => Math.abs(r.delta_s)));
  return (
    <ul className="bars">
      {rows.map((r) => (
        <li key={r.id} title={r.name}>
          <span className="bar-id">{r.id.length > 20 ? `${r.id.slice(0, 18)}…` : r.id}</span>
          <span className="bar-track">
            <span className={`bar ${r.delta_s > 0 ? "worse" : "better"}`} style={{ width: `${(Math.abs(r.delta_s) / max) * 100}%` }} />
          </span>
          <span className="bar-val">
            {signedMin(r.delta_s)} <span className="muted">· {r.class.replace("_", " ")} ×{r.weight}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

function WhyNotList({ data }: { data: ChartData }) {
  const list = whyNotList(data.preview);
  if (list.length === 0)
    return <p className="muted">No re-solve in this export: the decision has one precedence, or the export carries none.</p>;
  return (
    <ul className="whynot">
      {list.map((w) => (
        <li key={`${w.first}@${w.resource}`} className={`whynot-${whyNotState(w)}`}>
          {whyNotText(w)}
        </li>
      ))}
    </ul>
  );
}

export function CardPanel({ data, option, optionIdx, phase, now, preview, drawer, whyNot = false }: Props) {
  const p = data.preview;
  const shown = phase !== "none";
  const opt = option ?? p.options[optionIdx] ?? p.options[0];
  const left = expiresIn(p.valid_until_s, now);
  const accept = acceptState(opt, shown, left === 0);
  const top = topAffected(opt);
  return (
    <>
      <section className={`card ${shown ? "" : "closed"}`} aria-label="Recommendation card">
        <h3>
          Recommendation {p.id} {p.demo && <span className="demo">DEMO</span>}
          <span className={`expiry ${left < 600 ? "soon" : ""}`} title={`valid until ${hhmm(p.valid_until_s)}`}>
            {left === 0 ? "EXPIRED" : `expires in ${countdown(left)}`}
          </span>
        </h3>
        {!shown && <p className="muted">Press <kbd>P</kbd> to open the recommendation.</p>}
        {shown && (
          <>
            <p>{p.context}</p>
            <ol className="options">
              {contrastRows(p.options).map((r) => (
                <li key={r.id} className={r.key - 1 === optionIdx ? "chosen" : ""}>
                  <b>{r.key}</b> {r.id}: {r.title}
                  <span className="muted">
                    {" "}
                    · {signedMin(r.delta_min * 60)} weighted · {r.later} later / {r.earlier} earlier ·{" "}
                  </span>
                  <span className={r.verifier === "PASS" ? "pass" : "fail"}>{r.verifier}</span>
                  {r.first && <span className="muted"> · {r.first} first</span>}
                </li>
              ))}
              <li className="muted">Do nothing (baseline = the twin run)</li>
            </ol>
            <p className="muted">
              {contrastNote(p)}
              {p.options.length > MAX_OPTIONS ? ` Showing the first ${MAX_OPTIONS} of ${p.options.length}.` : ""} <kbd>Y</kbd> why not
            </p>
            {whyNot && (
              <>
                <h4>Why not</h4>
                <WhyNotList data={data} />
              </>
            )}
            <h4>Actions</h4>
            <ul className="actions">
              {opt.orders.map((o) => (
                <li key={o}>{o}</li>
              ))}
            </ul>
            <h4>The {top.length} most affected trains</h4>
            <Bars option={{ ...opt, impacts: top }} limit={top.length} />
            <p className="muted">
              Impact versus baseline: {opt.impacts.length} trains change, weighted delay {signedMin(opt.weighted_delay_delta_min * 60)}.
              Preview: {preview} (<kbd>V</kbd>).
            </p>
            <p>
              <span className={`badge badge-${opt.verification.status.toLowerCase()}`}>Verifier {opt.verification.status}</span>{" "}
              <span className="muted">
                {opt.verification.violations} violations · {opt.verification.warnings} warnings · {opt.verification.rules.length} rules
              </span>
            </p>
            <h4>Policy</h4>
            <p className="weights">
              Weights used (OD-04):{" "}
              {weightsUsed(opt, data.policy.weights).map(([k, v]) => (
                <span key={k} className="w">
                  {k.replace("_", " ")} {v}
                </span>
              ))}
            </p>
            <p>
              Hard rules:{" "}
              {opt.explanation.hard_rules.length === 0
                ? "none"
                : opt.explanation.hard_rules.map((r) => (
                    <span key={r.rule} className={`w ${r.applied ? "applied" : ""}`} title={r.note}>
                      {r.rule.replaceAll("_", " ")} {r.applied ? "(applied)" : "(not triggered)"}
                    </span>
                  ))}
            </p>
            <p className="muted">Confidence: {opt.confidence === null ? "n/a (demo)" : `${Math.round(opt.confidence * 100)} %`}</p>
            {phase === "card" && (
              <p className="keys">
                <span className={accept.enabled ? "" : "disabled"} title={accept.reason}>
                  <kbd>A</kbd> accept
                </span>{" "}
                · <kbd>M</kbd> modify · <kbd>R</kbd> reject · <kbd>D</kbd> details · <kbd>Esc</kbd> close
                {!accept.enabled && <span className="muted"> — accept disabled: {accept.reason}</span>}
              </p>
            )}
            {phase === "rejecting" && (
              <div className="reasons">
                <p>Reject: choose a reason (1-5), Esc cancels</p>
                <ol>
                  {REASONS.map((r) => (
                    <li key={r}>{r}</li>
                  ))}
                </ol>
              </div>
            )}
            {phase === "done" && <p className="muted">Decision recorded. Esc closes.</p>}
          </>
        )}
      </section>
      {shown && drawer && (
        <aside className="drawer" role="dialog" aria-label="Recommendation details">
          <h3>
            Details · {p.id} option {opt.id} <span className="muted">(<kbd>D</kbd> closes)</span>
          </h3>
          <h4>Control orders</h4>
          <ul className="actions">
            {opt.orders.map((o) => (
              <li key={o}>{o}</li>
            ))}
          </ul>
          <h4>Impact per train (weighted, worst first)</h4>
          <Bars option={opt} limit={12} />
          <h4>Why</h4>
          <p>{opt.explanation.why}</p>
          <h4>Why not the alternatives</h4>
          <p>{opt.explanation.why_not}</p>
          {p.options.length > 1 && (
            <>
              <h4>The options side by side</h4>
              <table className="contrast">
                <thead>
                  <tr>
                    <th>#</th>
                    <th>Option</th>
                    <th>Weighted delay</th>
                    <th>Later / earlier</th>
                    <th>Verifier</th>
                    <th>Serves first</th>
                    <th>Orders only this option has</th>
                  </tr>
                </thead>
                <tbody>
                  {contrastRows(p.options).map((r) => (
                    <tr key={r.id} className={r.key - 1 === optionIdx ? "chosen" : ""}>
                      <td>{r.key}</td>
                      <td>{r.id}</td>
                      <td>{signedMin(r.delta_min * 60)}</td>
                      <td>
                        {r.later} / {r.earlier}
                      </td>
                      <td className={r.verifier === "PASS" ? "pass" : "fail"}>{r.verifier}</td>
                      <td>{r.first ?? <span className="muted">-</span>}</td>
                      <td>
                        {r.sameAs ? <span className="muted">same orders as {r.sameAs}</span> : r.onlyHere.length === 0 ? <span className="muted">-</span> : r.onlyHere.join("; ")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="muted">{contrastNote(p)}</p>
            </>
          )}
          <h4>Why not X first?</h4>
          <WhyNotList data={data} />
          {data.policy.weights_in_explanations && (
            <p className="weights">
              Class weights: {Object.entries(opt.explanation.weights).map(([k, v]) => `${k.replace("_", " ")} ${v}`).join(" · ")}
            </p>
          )}
          <h4>Assumptions</h4>
          <ul>
            {opt.assumptions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
          <h4>Checks</h4>
          <p className="muted">{opt.verification.gate}</p>
          <ul className="rules">
            {opt.verification.rules.map((r) => (
              <li key={r.rule_id} className={`rule rule-${r.status.toLowerCase()}`}>
                {r.rule_id} <span>{r.status}</span>
                {r.violations > 0 ? ` (${r.violations})` : ""}
              </li>
            ))}
          </ul>
          <p>
            <label title="Role-gated: the analyst trace is for the analyst role">
              <input type="checkbox" disabled /> Analyst trace (analyst role only)
            </label>
          </p>
        </aside>
      )}
    </>
  );
}
