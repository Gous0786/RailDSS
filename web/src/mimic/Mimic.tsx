// Schematic mimic (FR-UI-06, issue #87): a straight-line SVG generated from the published layout, with
// the occupancy of the twin run at the shared time cursor. Supervisory only: there is no route setting.
import { useMemo } from "react";
import { hhmm } from "../chart/geometry";
import type { ChartData, MimicStation } from "../types";
import {
  LINES_TOP,
  LINE_PITCH,
  STATION_W,
  TRACK_Y,
  blockLineKey,
  height,
  icons,
  occupantsAt,
  stationLineKey,
  subBlockKey,
  trackY,
  width,
  xOf,
} from "./model";

const GAP = STATION_W / 2 + 4; // block lines stop short of the station bars

function lineLabel(s: MimicStation, i: number): string {
  const l = s.lines[i];
  const len = l.csr_m === null ? "" : ` · ${Math.round(l.csr_m)} m`;
  return `${s.code} line ${l.no}${l.name ? ` (${l.name})` : ""}: ${l.kind}${len}${l.platform ? " · platform" : ""}${
    l.in_use ? "" : " · OUT OF USE"
  }`;
}

export function Mimic({ data, now }: { data: ChartData; now: number }) {
  const m = data.mimic;
  const occ = useMemo(() => (m ? occupantsAt(m, now) : new Map<string, number[]>()), [m, now]);
  const trains = useMemo(() => icons(data, now, occ), [data, now, occ]);
  if (!m) return <div className="loading">This export has no mimic (format version {data.version}); regenerate it.</div>;
  const held = (key: string) => (occ.get(key)?.length ?? 0) > 0;
  const w = width(m);
  const h = height(m);
  return (
    <div className="mimic" aria-label="Schematic mimic">
      <div className="mimic-head">
        Schematic mimic · {hhmm(now)} · network {data.network_version} · {trains.length} trains in the section ·
        generated from the published layout; supervisory only
      </div>
      <div className="mimic-scroll">
        <svg width={w} height={h} role="img" aria-label={`Layout of ${data.network_version} with occupancy at ${hhmm(now)}`}>
          {Object.entries(TRACK_Y).map(([id, y]) => (
            <text key={id} x={4} y={y + 4} className="mimic-axis">
              {id}
            </text>
          ))}
          {m.blocks.map((b) =>
            b.lines.map((l) => {
              const y = trackY(l.line_id);
              const x0 = xOf(b.from_km) + GAP;
              const x1 = xOf(b.to_km) - GAP;
              if (x1 <= x0) return null;
              const n = Math.max(l.sub_blocks, 1);
              const step = (x1 - x0) / n;
              return Array.from({ length: n }, (_, k) => {
                const key = l.sub_blocks ? subBlockKey(b.segment, l.line_id, k + 1) : blockLineKey(b.segment, l.line_id);
                const busy = held(key) || held(blockLineKey(b.segment, l.line_id));
                return (
                  <line
                    key={`${b.segment}/${l.line_id}#${k}`}
                    x1={x0 + k * step + 1}
                    x2={x0 + (k + 1) * step - 1}
                    y1={y}
                    y2={y}
                    className={busy ? "mimic-track busy" : "mimic-track"}
                  >
                    <title>{`${b.from}-${b.to} ${l.line_id} (${b.system}${l.sub_blocks ? `, sub-block ${k + 1}/${n}` : ""})`}</title>
                  </line>
                );
              });
            }),
          )}
          {m.signals.map((s) =>
            s.km === null || !s.line_id ? null : (
              <path
                key={s.id}
                d={
                  s.direction === "UP"
                    ? `M${xOf(s.km)},${trackY(s.line_id) - 7} l-4,-3 l0,6 z`
                    : `M${xOf(s.km)},${trackY(s.line_id) + 7} l4,-3 l0,6 z`
                }
                className={`mimic-signal ${s.type}`}
              >
                <title>{`${s.id} (${s.type})`}</title>
              </path>
            ),
          )}
          {m.stations.map((s) => (
            <g key={s.code}>
              <line x1={xOf(s.km)} x2={xOf(s.km)} y1={40} y2={LINES_TOP - 10} className="mimic-station-tick" />
              <text x={xOf(s.km)} y={30} textAnchor="middle" className={s.halt ? "mimic-code halt" : "mimic-code"}>
                <title>{`${s.name} (km ${s.km})`}</title>
                {s.code}
              </text>
              {s.lines.map((l, i) => {
                const busy = held(stationLineKey(s.code, l.no));
                return (
                  <g key={l.no}>
                    <rect
                      x={xOf(s.km) - STATION_W / 2}
                      y={LINES_TOP + i * LINE_PITCH - 5}
                      width={STATION_W}
                      height={10}
                      rx={2}
                      className={`mimic-sl ${l.kind} ${busy ? "busy" : ""} ${l.in_use ? "" : "out"}`}
                    >
                      <title>{lineLabel(s, i)}</title>
                    </rect>
                    <text x={xOf(s.km) - STATION_W / 2 + 2} y={LINES_TOP + i * LINE_PITCH + 3} className="mimic-sl-text">
                      {l.kind === "loop" && l.csr_m !== null ? `L${l.no} ${Math.round(l.csr_m)}` : `${l.kind[0].toUpperCase()}${l.no}`}
                    </text>
                  </g>
                );
              })}
            </g>
          ))}
          {trains.map((t) => (
            <g key={t.id} className={`mimic-train ${t.colour}`} transform={`translate(${t.x},${t.y})`}>
              <title>{`${t.id} ${t.dir} on ${t.where}`}</title>
              <path d={t.dir === "DN" ? "M-14,-14 h24 l6,6 l-6,6 h-24 z" : "M14,-14 h-24 l-6,6 l6,6 h24 z"} />
              <text x={0} y={-4} textAnchor="middle">
                {t.label}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <div className="mimic-legend">
        <span className="swatch busy" /> occupied · <span className="swatch" /> free · loops show usable length (m) · train
        colour = delay (ok / yellow / amber / red) · ▶ DN, ◀ UP
      </div>
    </div>
  );
}

export default Mimic;
