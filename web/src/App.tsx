// RailDSS web UI: global chrome, control chart, KPI strip, train queue, alerts and the recommendation card
// (FR-UI-01..03, FR-UI-07..10, FR-HITL-12/13).

import { Suspense, lazy, useCallback, useEffect, useMemo, useState } from "react";
import { grafanaUrl, OSM_ATTRIBUTION, OSM_COPYRIGHT_URL } from "./aboutContent";
import type { PreviewMode } from "./chart/ControlChart";
import { type Window, defaultWindow, describe, hhmm, panWindow, zoomWindow } from "./chart/geometry";
import { AlertsPanel } from "./ops/AlertsPanel";
import { CardPanel, REASONS } from "./ops/CardPanel";
import type { AuditDoc } from "./audit/audit";
import { KpiStrip } from "./ops/KpiStrip";
import { QueuePanel } from "./ops/QueuePanel";
import { acknowledge, alertGroups, openCounts } from "./ops/alerts";
import { acceptState, expiresIn } from "./ops/card";
import { kpis } from "./ops/kpi";
import { HORIZON_S, NO_FILTER, type QueueFilter, type SortKey, filterRows, queueRows, sortRows } from "./ops/queue";
import type { Alert, ChartData, Priority } from "./types";

// The chart and the About page load as separate chunks (DR-0001 §3.3 condition 2; issue #89).
const ControlChart = lazy(() => import("./chart/ControlChart").then((m) => ({ default: m.ControlChart })));
const About = lazy(() => import("./About"));
const AuditView = lazy(() => import("./audit/AuditView").then((m) => ({ default: m.AuditView })));
const GRAFANA = grafanaUrl(import.meta.env as Record<string, string | undefined>);

type Mode = "REPLAY" | "SIM" | "SANDBOX";
type Phase = "none" | "card" | "rejecting" | "done";

const SIM_SPEED = 60; // SIM ×60: one simulated minute per second
const NOON_S = 12 * 3600;
const SORTS: SortKey[] = ["eta", "delay", "class", "dir", "train"];

const SHORTCUTS: [string, string][] = [
  ["P", "open the recommendation card"],
  ["1 / 2 / 3", "select an option (or a reject reason)"],
  ["V", "preview: off → before → after"],
  ["A", "accept the selected option (after the most affected trains are shown)"],
  ["M", "modify (sandbox)"],
  ["R", "reject (then choose a reason 1-5)"],
  ["D", "recommendation details drawer"],
  ["J / K", "next / previous alert"],
  ["Enter", "focus the chart on the alert"],
  ["X", "acknowledge the alert (and its related alerts)"],
  ["S", "queue: cycle the sort column"],
  ["F", "queue: flagged trains only"],
  ["Esc", "close / cancel"],
  ["← / →", "pan 15 min (Shift: 1 h)"],
  ["+ / −", "zoom in / out"],
  ["N", "jump to now"],
  ["Space", "play / pause the replay (SIM ×60)"],
  ["L", "audit and replay: log, timeline scrubbing, recommendation cards as shown"],
  ["I", "about, attributions and licences"],
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
  const [about, setAbout] = useState(false);
  const [audit, setAudit] = useState(false);
  const [auditDoc, setAuditDoc] = useState<AuditDoc | null>(null);
  const [auditError, setAuditError] = useState("");
  const [drawer, setDrawer] = useState(false);
  const [log, setLog] = useState<string[]>([]);
  const [acked, setAcked] = useState<ReadonlySet<string>>(new Set());
  const [cursor, setCursor] = useState(0);
  const [minPriority, setMinPriority] = useState<Priority>("P2");
  const [sort, setSort] = useState<SortKey>("eta");
  const [desc, setDesc] = useState(false);
  const [filter, setFilter] = useState<QueueFilter>(NO_FILTER);
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
    setAcked(new Set());
    fetch(`data/${day}.json`)
      .then((r) => r.json())
      .then((d: ChartData) => setData(d))
      .catch(() => setError(`Could not load data/${day}.json`));
  }, [day]);

  useEffect(() => {
    if (!audit || auditDoc) return;
    fetch("data/audit.json")
      .then((r) => r.json())
      .then((d: AuditDoc) => setAuditDoc(d))
      .catch(() => setAuditError("No audit log. Run: uv run python -m raildss_orchestrator run --from-h 8 --to-h 9 --audit-dir audit, then raildss_audit export --dir audit --out web/public/data/audit.json"));
  }, [audit, auditDoc]);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => setNow((t) => t + SIM_SPEED / 4), 250);
    return () => clearInterval(id);
  }, [playing]);

  const option = data && phase !== "none" ? (data.preview.options[optionIdx] ?? null) : null;
  const mode: Mode = sandbox ? "SANDBOX" : playing ? "SIM" : "REPLAY";
  const note = useCallback((text: string) => setLog((l) => [`${hhmm(now)}  ${text}`, ...l].slice(0, 6)), [now]);

  const groups = useMemo(
    () => (data ? alertGroups(data.alerts.filter((a) => a.time_s <= now), minPriority, acked) : []),
    [data, now, minPriority, acked],
  );
  const counts = useMemo(() => (data ? openCounts(data.alerts, now, acked) : { P1: 0, P2: 0, P3: 0, P4: 0 }), [data, now, acked]);
  const allRows = useMemo(() => (data ? queueRows(data, now, HORIZON_S, acked) : []), [data, now, acked]);
  const rows = useMemo(() => filterRows(sortRows(allRows, sort, desc), filter), [allRows, sort, desc, filter]);
  const expired = data ? expiresIn(data.preview.valid_until_s, now) === 0 : false;
  const pending = phase === "none" || phase === "card" || phase === "rejecting" ? (expired ? 0 : 1) : 0;
  const strip = useMemo(() => (data ? kpis(data, now, pending) : []), [data, now, pending]);
  const classes = useMemo(() => [...new Set((data?.trains ?? []).map((t) => t.class))], [data]);
  const day_counts = useMemo(() => {
    if (!data) return { pax: 0, frt: 0 };
    return {
      pax: data.trains.filter((t) => t.kind === "passenger").length,
      frt: data.trains.filter((t) => t.kind === "freight").length,
    };
  }, [data]);

  const closeCard = useCallback(() => {
    setPhase("none");
    setPreview("off");
    setDrawer(false);
  }, []);

  const focusAlert = useCallback((a: Alert) => {
    setSelected(a.trains[0] ?? null);
    setView(defaultWindow(a.time_s));
  }, []);

  const clickSort = useCallback(
    (k: SortKey) => {
      if (k === sort) setDesc((d) => !d);
      else {
        setSort(k);
        setDesc(false);
      }
    },
    [sort],
  );

  const onKey = useCallback(
    (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (!data || tag === "SELECT" || tag === "INPUT" || tag === "TEXTAREA") {
        if (e.key === "Escape" && (e.target as HTMLElement)?.blur) (e.target as HTMLElement).blur();
        return;
      }
      const k = e.key;
      const opts = data.preview.options;
      if (phase === "rejecting" && /^[1-5]$/.test(k)) {
        note(`Rejected ${data.preview.id} – reason: ${REASONS[Number(k) - 1]}`);
        setPhase("done");
        setPreview("off");
        return;
      }
      if (k === "Escape") {
        if (audit) setAudit(false);
        else if (about) setAbout(false);
        else if (help) setHelp(false);
        else if (drawer) setDrawer(false);
        else if (sandbox) setSandbox(false);
        else closeCard();
        return;
      }
      if (k === "?") return setHelp((h) => !h);
      if (k === "l" || k === "L") return setAudit((a) => !a);
      if (k === "i" || k === "I") return setAbout((a) => !a);
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
      if ((k === "d" || k === "D") && phase !== "none") return setDrawer((d) => !d);
      if (phase === "card" && (k === "a" || k === "A")) {
        const ok = acceptState(opts[optionIdx], true, expired);
        if (!ok.enabled) return note(`Accept is disabled: ${ok.reason}`);
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
      if (k === "j" || k === "J") return setCursor((c) => Math.min(Math.max(groups.length - 1, 0), c + 1));
      if (k === "k" || k === "K") return setCursor((c) => Math.max(0, c - 1));
      if (k === "x" || k === "X") {
        const g = groups[cursor];
        if (g) {
          setAcked((a) => acknowledge(a, g));
          note(`Acknowledged ${g.parent.id}${g.children.length ? ` and ${g.children.length} related` : ""}`);
        }
        return;
      }
      if (k === "Enter") {
        const g = groups[cursor];
        if (g) focusAlert(g.parent);
        return;
      }
      if (k === "s" || k === "S") {
        setSort((s) => SORTS[(SORTS.indexOf(s) + 1) % SORTS.length]);
        setDesc(false);
        return;
      }
      if (k === "f" || k === "F") return setFilter((f) => ({ ...f, flaggedOnly: !f.flaggedOnly }));
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
    [data, phase, help, about, audit, drawer, sandbox, optionIdx, now, expired, groups, cursor, note, closeCard, focusAlert],
  );

  useEffect(() => {
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onKey]);

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
        <span className={`alerts ${counts.P1 > 0 ? "p1" : ""}`} title="Open alerts P1/P2/P3">
          P1 {counts.P1} · P2 {counts.P2} · P3 {counts.P3}
        </span>
        <span className="grow" />
        <span>KTV–PSA · controller (demo)</span>
        <select value={day} onChange={(e) => setDay(e.target.value)} aria-label="Day">
          {days.map((d) => (
            <option key={d}>{d}</option>
          ))}
        </select>
        {GRAFANA && (
          <a href={GRAFANA} target="_blank" rel="noreferrer" title="System health and analyst time series (Grafana)">
            Health ↗
          </a>
        )}
        <button onClick={() => setAudit((a) => !a)} aria-label="Audit and replay">
          Audit
        </button>
        <button onClick={() => setAbout((a) => !a)} aria-label="About and attributions">
          About
        </button>
        <button onClick={() => setHelp((h) => !h)} aria-label="Keyboard shortcuts">
          ?
        </button>
      </header>

      {data && <KpiStrip items={strip} />}

      <main className="body">
        {error && <div className="error">{error}</div>}
        {!data && !error && <div className="loading">Loading {day}…</div>}
        {data && (
          <div className="main">
            <div className="chart-wrap">
              <Suspense fallback={<div className="loading">Loading chart…</div>}>
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
              </Suspense>
              <a className="map-attribution" href={OSM_COPYRIGHT_URL} target="_blank" rel="noreferrer">
                {OSM_ATTRIBUTION}
              </a>
            </div>
            <QueuePanel
              rows={rows}
              total={allRows.length}
              sort={sort}
              desc={desc}
              filter={filter}
              classes={classes}
              selected={selected}
              onSort={clickSort}
              onFilter={setFilter}
              onSelect={(id) => {
                setSelected(id);
                const t = data.trains.find((x) => x.id === id);
                if (t && t.actual.length > 0) setView(defaultWindow(Math.max(t.actual[0][0], now)));
              }}
            />
          </div>
        )}
        {data && (
          <aside className="side">
            <AlertsPanel
              groups={groups}
              now={now}
              acked={acked}
              cursor={cursor}
              minPriority={minPriority}
              onMinPriority={setMinPriority}
              onFocus={focusAlert}
              onAck={(g) => setAcked((a) => acknowledge(a, g))}
              onCursor={setCursor}
            />
            <CardPanel data={data} option={option} optionIdx={optionIdx} phase={phase} now={now} preview={preview} drawer={drawer} />
            <section>
              <h3>Day</h3>
              <p>
                {data.trains.length} trains ({day_counts.pax} passenger, {day_counts.frt} freight) · {data.stations.length}{" "}
                stations · network {data.network_version}
              </p>
              <p className="muted">
                Solid: twin (actual) · faint: WTT · dashed cyan: proposed. Twin run {data.completed ? "complete" : "incomplete"}.
                Alerts are what the twin observed (ground truth), not DSS output.
              </p>
              {selected && <p className="sel">Selected: {describe(data.trains.find((t) => t.id === selected)!)}</p>}
            </section>
            <section>
              <h3>Decision log</h3>
              {log.length === 0 ? <p className="muted">No decisions yet.</p> : log.map((l) => <p key={l}>{l}</p>)}
            </section>
          </aside>
        )}
        {audit && (
          <Suspense fallback={null}>
            <AuditView
              doc={auditDoc}
              error={auditError}
              now={now}
              onScrub={(t) => {
                setNow(t);
                setView(defaultWindow(t));
              }}
              onClose={() => setAudit(false)}
            />
          </Suspense>
        )}
        {about && (
          <Suspense fallback={null}>
            <About grafana={GRAFANA} onClose={() => setAbout(false)} />
          </Suspense>
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
