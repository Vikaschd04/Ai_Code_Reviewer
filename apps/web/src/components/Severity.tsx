import { useState } from "react";

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export type SeverityName = (typeof SEVERITIES)[number];

const LABELS: Record<SeverityName, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
  info: "Info",
};

/** Distinct glyph per level so severity is readable without colour (CVD, print, forced colours). */
export function SeverityMark({ severity }: { severity: string }) {
  const fill = `var(--sev-${severity})`;
  switch (severity) {
    case "critical":
      return (
        <svg className="sev-mark" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M5 0 10 5 5 10 0 5z" fill={fill} />
        </svg>
      );
    case "high":
      return (
        <svg className="sev-mark" viewBox="0 0 10 10" aria-hidden="true">
          <path d="M5 0.5 9.8 9.5H0.2z" fill={fill} />
        </svg>
      );
    case "medium":
      return (
        <svg className="sev-mark" viewBox="0 0 10 10" aria-hidden="true">
          <rect x="1" y="1" width="8" height="8" rx="1.5" fill={fill} />
        </svg>
      );
    case "low":
      return (
        <svg className="sev-mark" viewBox="0 0 10 10" aria-hidden="true">
          <circle cx="5" cy="5" r="4.2" fill={fill} />
        </svg>
      );
    default:
      return (
        <svg className="sev-mark" viewBox="0 0 10 10" aria-hidden="true">
          <circle cx="5" cy="5" r="3.6" fill="none" stroke={fill} strokeWidth="1.8" />
        </svg>
      );
  }
}

function isSeverity(value: string): value is SeverityName {
  return (SEVERITIES as readonly string[]).includes(value);
}

export function SeverityChip({ severity }: { severity: string }) {
  return (
    <span className="sev" data-severity={severity}>
      <SeverityMark severity={severity} />
      {isSeverity(severity) ? LABELS[severity] : severity}
    </span>
  );
}

export function severityCounts(bySeverity: unknown): Record<SeverityName, number> {
  const source = (bySeverity ?? {}) as Record<string, unknown>;
  const counts = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
  for (const name of SEVERITIES) {
    const value = source[name];
    counts[name] = typeof value === "number" ? value : 0;
  }
  return counts;
}

/** Part-to-whole: thin stacked bar (2px surface gaps), legend with glyph + label + count. */
export function SeverityStackBar({
  counts,
  label,
}: {
  counts: Record<SeverityName, number>;
  label: string;
}) {
  const [hover, setHover] = useState<SeverityName | null>(null);
  const total = SEVERITIES.reduce((sum, name) => sum + counts[name], 0);
  return (
    <figure className="stack-figure" aria-label={label}>
      {total === 0 ? (
        <div className="stackbar-empty" />
      ) : (
        <div
          className="stackbar"
          role="img"
          aria-label={`${label}: ${SEVERITIES.filter((n) => counts[n])
            .map((n) => `${counts[n]} ${n}`)
            .join(", ")}`}
        >
          {SEVERITIES.filter((name) => counts[name] > 0).map((name) => (
            <span
              key={name}
              className="stackbar-seg"
              style={{ flexGrow: counts[name], background: `var(--sev-${name})` }}
              onMouseEnter={() => {
                setHover(name);
              }}
              onMouseLeave={() => {
                setHover(null);
              }}
            >
              {hover === name ? (
                <span className="chart-tip">
                  {LABELS[name]}: {counts[name]} ({Math.round((counts[name] / total) * 100)}%)
                </span>
              ) : null}
            </span>
          ))}
        </div>
      )}
      <ul className="legend">
        {SEVERITIES.map((name) => (
          <li key={name}>
            <SeverityMark severity={name} />
            {LABELS[name]} <strong>{counts[name]}</strong>
          </li>
        ))}
      </ul>
    </figure>
  );
}

/** Magnitude by level: horizontal bars with the row label as identity and the value at the tip. */
export function SeverityBars({ counts }: { counts: Record<SeverityName, number> }) {
  const max = Math.max(1, ...SEVERITIES.map((name) => counts[name]));
  return (
    <ul className="barlist" aria-label="Findings by severity">
      {SEVERITIES.map((name) => (
        <li key={name} className="barlist-row">
          <SeverityChip severity={name} />
          <div className="barlist-track" title={`${LABELS[name]}: ${counts[name]}`}>
            <div
              className="barlist-bar"
              style={{ width: `${(counts[name] / max) * 100}%`, background: `var(--sev-${name})` }}
            />
          </div>
          <span className="barlist-value">{counts[name]}</span>
        </li>
      ))}
    </ul>
  );
}
