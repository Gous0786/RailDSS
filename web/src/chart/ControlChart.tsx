// Time-distance control chart on canvas (FR-UI-02, FR-UI-03; issue #86).
// In-house D3 + Canvas component (DR-0001 §3.4 fallback): d3-scale for the axes, canvas for drawing.

import { scaleLinear } from "d3-scale";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ChartData, Option, Point, Train } from "../types";
import { type Window, hhmm, nearestLine, panWindow, timeTickStep, visible, zoomWindow } from "./geometry";

export type PreviewMode = "off" | "before" | "after";

interface Props {
  data: ChartData;
  window: Window;
  now: number;
  option: Option | null;
  mode: PreviewMode;
  selected: string | null;
  onWindow: (w: Window) => void;
  onSelect: (id: string | null) => void;
}

const MARGIN = { left: 170, right: 16, top: 30, bottom: 12 };
const COLOURS = {
  bg: "#14171c",
  grid: "#2a2f37",
  halt: "#20242b",
  text: "#c9d1d9",
  muted: "#7d8590",
  passenger: "#7fa7d4",
  freight: "#b9a37a",
  booked: "rgba(220, 225, 235, 0.16)",
  now: "#e6edf3",
  proposed: "#3fd0c9",
  highlight: "#ffffff",
};

function short(t: Train): string {
  return t.id.split("@")[0].split(" ")[0].slice(0, 8);
}

export function ControlChart({ data, window: w, now, option, mode, selected, onWindow, onSelect }: Props) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 800, height: 500 });
  const [hover, setHover] = useState<string | null>(null);
  const drag = useRef<{ x: number; window: Window } | null>(null);

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      setSize({ width: Math.max(300, entry.contentRect.width), height: Math.max(240, entry.contentRect.height) });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const kmExtent = useMemo(() => {
    const kms = data.stations.map((s) => s.km);
    return [Math.min(...kms), Math.max(...kms)] as [number, number];
  }, [data]);

  const x = scaleLinear()
    .domain([w.start, w.end])
    .range([MARGIN.left, size.width - MARGIN.right]);
  const y = scaleLinear()
    .domain(kmExtent)
    .range([MARGIN.top + 8, size.height - MARGIN.bottom - 8]);

  const changed = option && mode !== "off" ? option.trains : {};

  // Screen-space actual lines of the visible trains (for hit-testing and drawing).
  const screen = useMemo(() => {
    return data.trains.map((t) => visible(t.actual, w.start, w.end).map(([ts, km]) => [x(ts), y(km)] as Point));
  }, [data, w.start, w.end, size.width, size.height, kmExtent]);

  useEffect(() => {
    const c = canvas.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.width * dpr;
    c.height = size.height * dpr;
    const g = c.getContext("2d");
    if (!g) return;
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.fillStyle = COLOURS.bg;
    g.fillRect(0, 0, size.width, size.height);
    g.font = "12px system-ui, sans-serif";
    g.textBaseline = "middle";

    // stations (distance axis): block stations with loops, halts thin
    for (const s of data.stations) {
      const yy = y(s.km);
      g.strokeStyle = s.halt ? COLOURS.halt : COLOURS.grid;
      g.setLineDash(s.halt ? [2, 4] : []);
      g.beginPath();
      g.moveTo(MARGIN.left, yy);
      g.lineTo(size.width - MARGIN.right, yy);
      g.stroke();
      g.setLineDash([]);
      g.fillStyle = s.halt ? COLOURS.muted : COLOURS.text;
      g.textAlign = "left";
      if (s.halt) {
        g.font = "10px system-ui, sans-serif";
        g.fillText(s.code, 8, yy);
      } else {
        g.font = "bold 12px system-ui, sans-serif";
        g.fillText(s.code, 8, yy);
        g.font = "10px system-ui, sans-serif";
        g.fillStyle = COLOURS.muted;
        const loops = s.loops ? `${s.loops} loop${s.loops > 1 ? "s" : ""}${s.max_loop_m ? ` ≤${Math.round(s.max_loop_m)} m` : ""}` : "no loop";
        g.fillText(loops, 52, yy);
      }
    }
    g.font = "11px system-ui, sans-serif";

    // time axis
    const step = timeTickStep(w.end - w.start, size.width - MARGIN.left - MARGIN.right);
    g.textAlign = "center";
    for (let t = Math.ceil(w.start / step) * step; t <= w.end; t += step) {
      const xx = x(t);
      g.strokeStyle = COLOURS.grid;
      g.beginPath();
      g.moveTo(xx, MARGIN.top);
      g.lineTo(xx, size.height - MARGIN.bottom);
      g.stroke();
      g.fillStyle = COLOURS.muted;
      g.fillText(hhmm(t), xx, MARGIN.top / 2);
    }

    g.save();
    g.beginPath();
    g.rect(MARGIN.left, MARGIN.top, size.width - MARGIN.left - MARGIN.right, size.height - MARGIN.top - MARGIN.bottom);
    g.clip();

    const path = (pts: Point[]) => {
      g.beginPath();
      pts.forEach(([ts, km], i) => (i ? g.lineTo(x(ts), y(km)) : g.moveTo(x(ts), y(km))));
      g.stroke();
    };

    // WTT ghost lines
    g.strokeStyle = COLOURS.booked;
    g.lineWidth = 1;
    for (const t of data.trains) {
      if (t.booked.length) path(visible(t.booked, w.start, w.end));
    }

    // actual lines and labels
    data.trains.forEach((t, i) => {
      const pts = visible(t.actual, w.start, w.end);
      if (pts.length < 2) return;
      const isChanged = t.id in changed;
      const focus = t.id === selected || t.id === hover;
      g.strokeStyle = focus ? COLOURS.highlight : t.kind === "passenger" ? COLOURS.passenger : COLOURS.freight;
      g.globalAlpha = isChanged && mode === "after" ? 0.25 : 1;
      g.lineWidth = focus ? 2.6 : isChanged && mode === "before" ? 2.2 : 1.3;
      path(pts);
      g.globalAlpha = 1;
      // label where the line starts inside the view (not at the clipped edge), or when focused
      if (focus || t.actual[0][0] >= w.start) {
        const [lx, ly] = focus ? (screen[i][0] ?? [x(pts[0][0]), y(pts[0][1])]) : [x(t.actual[0][0]), y(t.actual[0][1])];
        g.fillStyle = focus ? COLOURS.highlight : COLOURS.muted;
        g.textAlign = "left";
        g.fillText(short(t), Math.max(MARGIN.left + 2, lx + 3), ly - 7);
      }
    });

    // recommendation preview: proposed lines in a distinct style (FR-UI-03)
    if (mode === "after") {
      g.strokeStyle = COLOURS.proposed;
      g.lineWidth = 2.2;
      g.setLineDash([6, 4]);
      for (const pts of Object.values(changed)) path(visible(pts, w.start, w.end));
      g.setLineDash([]);
    }

    // now line
    if (now >= w.start && now <= w.end) {
      g.strokeStyle = COLOURS.now;
      g.lineWidth = 1;
      g.setLineDash([3, 3]);
      g.beginPath();
      g.moveTo(x(now), MARGIN.top);
      g.lineTo(x(now), size.height - MARGIN.bottom);
      g.stroke();
      g.setLineDash([]);
    }
    g.restore();
    if (now >= w.start && now <= w.end) {
      g.fillStyle = COLOURS.now;
      g.textAlign = "center";
      g.fillText(`now ${hhmm(now)}`, x(now), MARGIN.top - 6);
    }
  });

  const timeAt = (clientX: number) => {
    const r = canvas.current?.getBoundingClientRect();
    return x.invert(clientX - (r?.left ?? 0));
  };

  return (
    <div className="chart" ref={box}>
      <canvas
        ref={canvas}
        style={{ width: size.width, height: size.height }}
        aria-label={`Time-distance chart of ${data.day}, ${data.trains.length} trains`}
        role="img"
        onWheel={(e) => onWindow(zoomWindow(w, e.deltaY > 0 ? 1.2 : 1 / 1.2, timeAt(e.clientX)))}
        onMouseDown={(e) => (drag.current = { x: e.clientX, window: w })}
        onMouseUp={(e) => {
          const d = drag.current;
          drag.current = null;
          if (d && Math.abs(e.clientX - d.x) < 3) {
            const r = canvas.current!.getBoundingClientRect();
            const i = nearestLine(screen, e.clientX - r.left, e.clientY - r.top);
            onSelect(i >= 0 ? data.trains[i].id : null);
          }
        }}
        onMouseLeave={() => {
          drag.current = null;
          setHover(null);
        }}
        onMouseMove={(e) => {
          const d = drag.current;
          if (d) {
            const secondsPerPx = (d.window.end - d.window.start) / (size.width - MARGIN.left - MARGIN.right);
            onWindow(panWindow(d.window, -(e.clientX - d.x) * secondsPerPx));
            return;
          }
          const r = canvas.current!.getBoundingClientRect();
          const i = nearestLine(screen, e.clientX - r.left, e.clientY - r.top);
          setHover(i >= 0 ? data.trains[i].id : null);
        }}
      />
      {hover && <div className="tip">{describe(data.trains.find((t) => t.id === hover)!)}</div>}
    </div>
  );
}

export function describe(t: Train): string {
  return `${t.id} · ${t.name || t.category || t.kind} · ${t.kind}`;
}
