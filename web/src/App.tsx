// RailDSS web UI shell: global chrome, control chart, recommendation preview (FR-UI-01..03, FR-HITL-12).

import { useCallback, useEffect, useMemo, useState } from "react";
import { ControlChart, type PreviewMode, describe } from "./chart/ControlChart";
import { type Window, defaultWindow, hhmm, panWindow, zoomWindow } from "./chart/geometry";
import type { ChartData } from "./types";

type Mode = "REPLAY" | "SIM" | "SANDBOX";
type Phase = "none" | "card" | "rejecting" | "done";

// FR-HITL-05 reject reason codes
const REASONS = [
  "Unsafe / local knowledge (loop or line not usable)",
  "Crew / loco issue",
  "Priority instruction from Dy. CHC / higher authority",
  "Data wrong / ETA disagreement",
  "Other",
];
const SIM_SPEED = 60; // SIM ×60: one simulated minute per second
const NOON_S = 12 * 3600;

const SHORTCUTS: [string, string][] = [
  ["P", "open the recommendation card"],
  ["1 / 2 / 3", "select an option (or a reject reason)"],
  ["V", "preview: off → before → after"],
  ["A", "accept the selected option"],
  ["M", "modify (sandbox)"],
  ["R", "reject (then choose a reason 1-5)"],
  ["Esc", "close / cancel"],
  ["← / →", "pan 15 min (Shift: 1 h)"],
  ["+ / −", "zoom in / out"],
  ["N", "jump to now"],
  ["Space", "play / pause the replay (SIM ×60)"],
  ["?", "show / hide this help"],
];

function useIstClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return now.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour12: false });
}

export default function App() {
  const [days, setDays] = useState<string[]>([]);
  const [day, setDay] = useState<string>("");
  const [data, setData] = useState<ChartData | null>(null);
  const [error, setError] = useState<string>("");
  const [now, setNow] = useState(NOON_S);
  const [view, setView] = useState<Window>(defaultWindow(NOON_S));
  const [playing, setPlaying] = useState(false);
  const [sandbox, setSandbox] = useState(false);
  const [phase, setPhase] = useState<Phase>("none");
  const [optionIdx, setOptionIdx] = useState(0);
  const [preview, setPreview] = useState<PreviewMode>("off");
  const [selected, setSelected] = useState<string | null>(null);
  const [help, setHelp] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const ist = useIstClock();

  useEffect(() => {
    fetch("data/index.json")
      .then((r) => r.json())
      .then((idx: { days: string[] }) => {
        setDays(idx.days);
        setDay(idx.days[idx.days.length - 1] ?? "");
      })
      .catch(() => setError("No chart data. Run: uv run python -m raildss_twin chart --date 2025-08-20"));
  }, []);

  useEffect(() => {
    if (!day) return;
    setData(null);
    fetch(`data/${day}.json`)
      .then((r) => r.json())
      .then((d: ChartData) => setData(d))
      .catch(() => setError(`Could not load data/${day}.json`));
  }, [day]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setNow((t) => t + SIM_SPEED / 4), 250);
    return () => clearInterval(id);
  }, [playing]);

  const option = data && phase !== "none" ? (data.preview.options[optionIdx] ?? null) : null;
  const mode: Mode = sandbox ? "SANDBOX" : playing ? "SIM" : "REPLAY";
  const note = useCallback((text: string) => setLog((l) => [`${hhmm(now)}  ${text}`, ...l].slice(0, 6)), [now]);

  const closeCard = useCallback(() => {
    setPhase("none");
    setPreview("off");
  }, []);

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      if (!data || (e.target as HTMLElement)?.tagName === "SELECT") return;
      const k = e.key;
      const opts = data.preview.options;
      if (phase === "rejecting" && /^[1-5]$/.test(k)) {
        note(`Rejected ${data.preview.id} – reason: ${REASONS[Number(k) - 1]}`);
        setPhase("done");
        setPreview("off");
        return;
      }
      if (k === "Escape") {
        if (help) setHelp(false);
        else if (sandbox) setSandbox(false);
        else closeCard();
        return;
      }
      if (k === "?") return setHelp((h) => !h);
      if (k === "p" || k === "P") {
        setPhase("card");
        setPreview("after");
        return;
      }
      if (phase === "card" && /^[1-3]$/.test(k) && Number(k) <= opts.length) {
        setOptionIdx(Number(k) - 1);
        return;
      }
      if ((k === "v" || k === "V") && phase !== "none") {
        setPreview((m) => (m === "off" ? "before" : m === "before" ? "after" : "off"));
        return;
      }
      if (phase === "card" && (k === "a" || k === "A")) {
        note(`Accepted ${data.preview.id} option ${opts[optionIdx].id}: ${opts[optionIdx].orders.join("; ")} (demo, no orders sent)`);
        setPhase("done");
        setPreview("off");
        return;
      }
      if (phase === "card" && (k === "m" || k === "M")) {
        setSandbox(true);
        note(`Modify ${data.preview.id} option ${opts[optionIdx].id}: sandbox opened (edit forms: FR-HITL-04)`);
        return;
      }
      if (phase === "card" && (k === "r" || k === "R")) {
        setPhase("rejecting");
        return;
      }
      if (k === "ArrowLeft" || k === "ArrowRight") {
        const step = (e.shiftKey ? 3600 : 900) * (k === "ArrowLeft" ? -1 : 1);
        setView((w) => panWindow(w, step));
        return;
      }
      if (k === "+" || k === "=") return setView((w) => zoomWindow(w, 1 / 1.5, (w.start + w.end) / 2));
      if (k === "-" || k === "_") return setView((w) => zoomWindow(w, 1.5, (w.start + w.end) / 2));
      if (k === "n" || k === "N") return setView(defaultWindow(now));
      if (k === " ") {
        e.preventDefault();
        setPlaying((p) => !p);
      }
    },
    [data, phase, help, sandbox, optionIdx, now, note, closeCard],
  );

  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

  const counts = useMemo(() => {
    if (!data) return { pax: 0, frt: 0 };
    return {
      pax: data.trains.filter((t) => t.kind === "passenger").length,
      frt: data.trains.filter((t) => t.kind === "freight").length,
    };
  }, [data]);

  return (
    <div className={`app mode-${mode.toLowerCase()}`}>
      <header className="chrome">
        <span className={`banner banner-${mode.toLowerCase()}`} title="Mode (FR-UI-01)">
          {mode === "REPLAY" ? "⏵ REPLAY" : mode === "SIM" ? `⏩ SIM ×${SIM_SPEED}` : "✎ SANDBOX"}
        </span>
        <span>
          IST <b>{ist}</b>
        </span>
        <span>
          sim <b>{hhmm(now)}</b> {day}
        </span>
        <span className="fresh" title="Data freshness">
          ● twin ground truth
        </span>
        <span className="alerts" title="Alerts P1/P2/P3">
          P1 0 · P2 0 · P3 0
        </span>
        <span className="grow" />
        <span>KTV–PSA · controller (demo)</span>
        <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
          {days.map((d) => (
            <option key={d}>{d}</option>
          ))}
        </select>
        <button onClick={() => setHelp((h) => !h)} aria-label="Keyboard shortcuts">
          ?
        </button>
      </header>

      <main className="body">
        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="loading">Loading {day}…</div>}
        {data && (
          <ControlChart
            data={data}
            window={view}
            now={now}
            option={option}
            mode={preview}
            selected={selected}
            onWindow={setView}
            onSelect={setSelected}
          />
        )}
        {data && (
          <aside className="side">
            <section>
              <h3>Day</h3>
              <p>
                {data.trains.length} trains ({counts.pax} passenger, {counts.frt} freight) · {data.stations.length}{" "}
                stations · network {data.network_version}
              </p>
              <p className="muted">
                Solid: twin (actual) · faint: WTT · dashed cyan: proposed. Twin run{" "}
                {data.completed ? "complete" : "incomplete"}.
              </p>
              {selected && <p className="sel">Selected: {describe(data.trains.find((t) => t.id === selected)!)}</p>}
            </section>
            <section className={`card ${phase === "none" ? "closed" : ""}`}>
              <h3>
                Recommendation {data.preview.id} {data.preview.demo && <span className="demo">DEMO</span>}
              </h3>
              {phase === "none" && <p className="muted">Press P to open the recommendation.</p>}
              {phase !== "none" && (
                <>
                  <p>{data.preview.context}</p>
                  <ol>
                    {data.preview.options.map((o, i) => (
                      <li key={o.id} className={i === optionIdx ? "chosen" : ""}>
                        <b>{o.id}</b> {o.title}{" "}
                        <span className="muted">({Object.keys(o.trains).length} trains change)</span>
                      </li>
                    ))}
                    <li className="muted">Do nothing (baseline = the twin run)</li>
                  </ol>
                  <p className="muted">Preview: {preview} (V toggles before / after)</p>
                  {phase === "card" && <p className="keys">A accept · M modify · R reject · Esc close</p>}
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
            <section>
              <h3>Decision log</h3>
              {log.length === 0 ? <p className="muted">No decisions yet.</p> : log.map((l) => <p key={l}>{l}</p>)}
            </section>
          </aside>
        )}
        {help && (
          <div className="help" role="dialog" aria-label="Keyboard shortcuts">
            <h3>Keyboard shortcuts</h3>
            <table>
              <tbody>
                {SHORTCUTS.map(([k, d]) => (
                  <tr key={k}>
                    <td>
                      <kbd>{k}</kbd>
                    </td>
                    <td>{d}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="muted">Mouse: wheel zooms, drag pans, click selects a train.</p>
          </div>
        )}
      </main>
    </div>
  );
}
