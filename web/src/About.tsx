// About page: attributions and third-party notices (DR-0001 §3.3, §4, §5.3; FR-DATA-05; issue #89).

import {
  CHART_NOTE,
  COPERNICUS_ATTRIBUTION,
  COPERNICUS_DISCLAIMER,
  GRAFANA_NOTE,
  LIBRARIES,
  ODBL_URL,
  OSM_ATTRIBUTION,
  OSM_COPYRIGHT_URL,
} from "./aboutContent";

interface Props {
  grafana: string;
  onClose: () => void;
}

export default function About({ grafana, onClose }: Props) {
  return (
    <div className="about" role="dialog" aria-label="About RailDSS">
      <div className="about-head">
        <h3>About RailDSS</h3>
        <button onClick={onClose} aria-label="Close About">
          ✕
        </button>
      </div>
      <p>
        RailDSS is a decision-support prototype for train control on the Kottavalasa–Palasa (KTV–PSA) corridor. This demo
        replays a twin day; nothing here sends orders to a railway. Source code: Apache-2.0
        (<a href="https://github.com/Gous0786/RailDSS" target="_blank" rel="noreferrer">github.com/Gous0786/RailDSS</a>).
      </p>
      <h4>Data</h4>
      <ul>
        <li>
          Network, timetable and freight data for KTV–PSA: supplied by the project owner (see <code>NOTICE</code>).
        </li>
        <li>
          Gradients, curves and yard lengths use <b>{OSM_ATTRIBUTION}</b>, licensed under the{" "}
          <a href={ODBL_URL} target="_blank" rel="noreferrer">
            ODbL 1.0
          </a>
          . <a href={OSM_COPYRIGHT_URL} target="_blank" rel="noreferrer">openstreetmap.org/copyright</a>
        </li>
        <li>
          Gradient gap-fill: {COPERNICUS_ATTRIBUTION}. <span className="muted">{COPERNICUS_DISCLAIMER}</span>
        </li>
      </ul>
      <h4>Components</h4>
      <p>{CHART_NOTE}</p>
      <table>
        <tbody>
          {LIBRARIES.map((l) => (
            <tr key={l.name}>
              <td>
                <a href={l.url} target="_blank" rel="noreferrer">
                  {l.name}
                </a>{" "}
                {l.version}
              </td>
              <td>{l.licence}</td>
              <td className="muted">{l.role}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted">The full production dependency report is produced by CI (<code>npm run licences</code>).</p>
      <h4>Observability</h4>
      <p>
        {GRAFANA_NOTE}{" "}
        {grafana ? (
          <a href={grafana} target="_blank" rel="noreferrer">
            Open Grafana
          </a>
        ) : (
          <span className="muted">No Grafana is configured for this build (set VITE_GRAFANA_URL).</span>
        )}
      </p>
    </div>
  );
}
