import { useState } from "react";

import type { HealthPoint } from "../api/client";
import { fetchHealthHistory } from "../api/endpoints";
import { useAsync } from "../lib/useAsync";
import { Disclosure } from "./Common";
import { Icon } from "./Icon";

// Health over time (ADD-A6): what each review found. Columns are open issues per review: the
// critical and high part sits on the baseline (the part to compare), medium and low above it,
// in a validated two-step ramp of one hue; the tiles give the latest numbers and the change.

const HEIGHT = 168;
const TOP = 12;
const BOTTOM = 24;
const LEFT = 32;
const BAR = 24;
const GAP = 2;
const RADIUS = 4;

function severe(point: HealthPoint): number {
  return (point.issues.critical ?? 0) + (point.issues.high ?? 0);
}

function total(point: HealthPoint): number {
  return Object.values(point.issues).reduce((sum, n) => sum + n, 0);
}

function needingWork(point: HealthPoint): number {
  return Object.values(point.failing).reduce((sum, n) => sum + n, 0);
}

function day(value: string | null | undefined, withTime = false): string {
  if (!value) return "—";
  const options: Intl.DateTimeFormatOptions = withTime
    ? { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }
    : { day: "numeric", month: "short" };
  return new Intl.DateTimeFormat(undefined, options).format(new Date(value));
}

/** Review labels: the day, or day and time when two reviews share a day. */
function labels(points: HealthPoint[]): string[] {
  const days = points.map((p) => day(p.reviewed_at));
  const repeated = new Set(days).size < days.length;
  return repeated ? points.map((p) => day(p.reviewed_at, true)) : days;
}

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const step = 10 ** Math.floor(Math.log10(value));
  return Math.ceil(value / step) * step;
}

function change(now: number, before: number, noun: string): string {
  if (now === before) return "Same as the previous review";
  const diff = Math.abs(now - before);
  return `${String(diff)} ${now < before ? "fewer" : "more"} ${noun} than the previous review`;
}

/** The tooltip's anchor in percent, kept away from the edges so it never leaves the card. */
function tipX(index: number, band: number, width: number): number {
  return Math.min(75, Math.max(25, ((LEFT + band * (index + 0.5)) / width) * 100));
}

/** A column path: square at the baseline, 4px rounded data end on top. */
function column(x: number, y: number, width: number, height: number, round: boolean): string {
  if (height <= 0) return "";
  const r = round ? Math.min(RADIUS, height, width / 2) : 0;
  return [
    `M${String(x)},${String(y + height)}`,
    `V${String(y + r)}`,
    r ? `Q${String(x)},${String(y)} ${String(x + r)},${String(y)}` : "",
    `H${String(x + width - r)}`,
    r ? `Q${String(x + width)},${String(y)} ${String(x + width)},${String(y + r)}` : "",
    `V${String(y + height)}`,
    "Z",
  ].join(" ");
}

function Tile({ value, label, delta }: { value: number; label: string; delta: string }) {
  return (
    <div className="tile">
      <span className="tile-value">{value}</span>
      <span className="tile-label">{label}</span>
      <span className="tile-delta">{delta}</span>
    </div>
  );
}

export function HealthTrend({ projectId }: { projectId: string }) {
  const history = useAsync((signal) => fetchHealthHistory(projectId, signal), [projectId]);
  const [active, setActive] = useState<number | null>(null);
  const points = history.data?.points ?? [];
  if (points.length < 2) return null; // a trend needs two reviews
  const latest = points[points.length - 1];
  const previous = points[points.length - 2];
  if (!latest || !previous) return null;
  const band = 56;
  const width = LEFT + band * points.length;
  const plot = HEIGHT - TOP - BOTTOM;
  const max = niceMax(Math.max(...points.map(total), 1));
  const y = (n: number) => TOP + plot - (n / max) * plot;
  const shown = active === null ? null : points[active];
  const names = labels(points);
  // The axis names a day once (under its first review); the tooltip and table give the time.
  const ticks = points.map((p, i) =>
    i > 0 && day(p.reviewed_at) === day(points[i - 1]?.reviewed_at) ? "" : day(p.reviewed_at),
  );
  return (
    <section className="card stack" aria-labelledby="trend-title" data-testid="health-trend">
      <div className="stack stack-xs">
        <h2 id="trend-title" className="card-title">
          <Icon name="pulse" size={16} /> Health over time
        </h2>
        <p className="card-sub">
          What each of the last {points.length} reviews found. Insights keeps an issue open until a
          check shows it gone, so it can list more than the latest review found.
        </p>
      </div>
      <div className="tiles tiles-3" data-testid="trend-tiles">
        <Tile
          value={total(latest)}
          label="Open issues"
          delta={change(total(latest), total(previous), "issues")}
        />
        <Tile
          value={severe(latest)}
          label="Critical and high"
          delta={change(severe(latest), severe(previous), "issues")}
        />
        <Tile
          value={needingWork(latest)}
          label="Checkpoints needing work"
          delta={change(needingWork(latest), needingWork(previous), "checkpoints")}
        />
      </div>
      <figure className="trend-figure">
        <div className="trend-scroll">
          <svg
            className="trend-chart"
            width={width}
            height={HEIGHT}
            viewBox={`0 0 ${String(width)} ${String(HEIGHT)}`}
            role="group"
            aria-label="Open issues per review: critical and high, medium and low. The table below lists the same numbers."
          >
            {[0, max / 2, max].map((tick) => (
              <g key={tick}>
                <line className="trend-grid" x1={LEFT} x2={width} y1={y(tick)} y2={y(tick)} />
                <text className="trend-axis" x={LEFT - 6} y={y(tick) + 4} textAnchor="end">
                  {Math.round(tick)}
                </text>
              </g>
            ))}
            {points.map((point, index) => {
              const x = LEFT + band * index + (band - BAR) / 2;
              const strong = severe(point);
              const rest = total(point) - strong;
              const strongTop = y(strong);
              const restTop = y(strong + rest);
              return (
                <g
                  key={point.scan_id}
                  className="trend-column"
                  tabIndex={0}
                  role="img"
                  aria-label={`${names[index] ?? ""}: ${String(strong)} critical and high, ${String(rest)} medium and low, ${String(needingWork(point))} checkpoints needing work`}
                  onPointerEnter={() => {
                    setActive(index);
                  }}
                  onPointerLeave={() => {
                    setActive(null);
                  }}
                  onFocus={() => {
                    setActive(index);
                  }}
                  onBlur={() => {
                    setActive(null);
                  }}
                >
                  <rect
                    className="trend-band"
                    x={LEFT + band * index}
                    y={TOP}
                    width={band}
                    height={plot}
                  />
                  <path
                    d={column(x, strongTop, BAR, y(0) - strongTop, rest === 0)}
                    fill="var(--trend-strong)"
                  />
                  {rest > 0 ? (
                    <path
                      d={column(
                        x,
                        restTop,
                        BAR,
                        Math.max(0, strongTop - restTop - (strong ? GAP : 0)),
                        true,
                      )}
                      fill="var(--trend-light)"
                    />
                  ) : null}
                  <text
                    className="trend-axis"
                    x={LEFT + band * index + band / 2}
                    y={HEIGHT - 6}
                    textAnchor="middle"
                  >
                    {ticks[index]}
                  </text>
                </g>
              );
            })}
          </svg>
        </div>
        {shown && active !== null ? (
          <div
            className="chart-tip trend-tip"
            role="status"
            style={{ ["--tip-x" as string]: `${String(tipX(active, band, width))}%` }}
          >
            <strong>{names[active]}</strong>
            {shown.upload ? <span className="muted"> · {shown.upload}</span> : null}
            <br />
            <strong>{severe(shown)}</strong> critical and high
            <br />
            <strong>{total(shown) - severe(shown)}</strong> medium and low
            <br />
            <strong>{needingWork(shown)}</strong> checkpoints needing work
          </div>
        ) : null}
        <ul className="legend">
          <li>
            <span className="legend-key" style={{ background: "var(--trend-strong)" }} /> Critical
            and high
          </li>
          <li>
            <span className="legend-key" style={{ background: "var(--trend-light)" }} /> Medium and
            low
          </li>
        </ul>
      </figure>
      <Disclosure summary="Numbers per review" testId="trend-table">
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col">Review</th>
                <th scope="col">Critical</th>
                <th scope="col">High</th>
                <th scope="col">Medium</th>
                <th scope="col">Low</th>
                <th scope="col">Checkpoints needing work</th>
              </tr>
            </thead>
            <tbody>
              {points.map((point, index) => (
                <tr key={point.scan_id}>
                  <td>{names[index]}</td>
                  <td>{point.issues.critical ?? 0}</td>
                  <td>{point.issues.high ?? 0}</td>
                  <td>{point.issues.medium ?? 0}</td>
                  <td>{point.issues.low ?? 0}</td>
                  <td>{needingWork(point)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Disclosure>
    </section>
  );
}
