import type { EngineRun } from "../api/client";
import { categoryLabel } from "../lib/labels";

/** Single-series magnitude bars (one hue; the heading names the series, so no legend). */
export function CategoryBars({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(([, value]) => value));
  if (entries.length === 0) return <p className="muted small">No findings.</p>;
  return (
    <ul className="barlist">
      {entries.map(([name, value]) => (
        <li key={name} className="barlist-row">
          <span className="secondary">{categoryLabel(name)}</span>
          <div className="barlist-track" title={`${categoryLabel(name)}: ${value}`}>
            <div
              className="barlist-bar"
              style={{ width: `${(value / max) * 100}%`, background: "var(--sev-low)" }}
            />
          </div>
          <span className="barlist-value">{value}</span>
        </li>
      ))}
    </ul>
  );
}

/** Eligible-file coverage as a stacked meter: analyzed / failed / not attempted, with legend. */
export function CoverageMeter({ run }: { run: EngineRun }) {
  const eligible = run.files_eligible;
  const analyzed = run.files_succeeded;
  const failed = run.files_failed;
  const skipped = Math.max(0, eligible - analyzed - failed);
  const parts = [
    { key: "analyzed", value: analyzed, color: "var(--meter-analyzed)", label: "analyzed" },
    { key: "failed", value: failed, color: "var(--meter-failed)", label: "failed" },
    { key: "skipped", value: skipped, color: "var(--meter-skipped)", label: "not attempted" },
  ].filter((part) => part.value > 0);
  return (
    <div className="meter-block">
      {eligible === 0 ? (
        <div className="meter meter-empty" role="img" aria-label="No matching files" />
      ) : (
        <div
          className="meter"
          role="img"
          aria-label={`${analyzed} of ${eligible} eligible files analyzed, ${failed} failed, ${skipped} not attempted`}
        >
          {parts.map((part) => (
            <span
              key={part.key}
              title={`${part.value} ${part.label}`}
              style={{ flexGrow: part.value, background: part.color }}
            />
          ))}
        </div>
      )}
      <p className="meter-caption">
        {eligible === 0 ? (
          "No matching files in this upload"
        ) : (
          <>
            <span className="strong">
              {analyzed} of {eligible}
            </span>{" "}
            files checked{failed ? ` · ${failed} could not be read` : ""}
            {skipped ? ` · ${skipped} not attempted` : ""}
          </>
        )}
      </p>
    </div>
  );
}

export function StatTile({
  label,
  value,
  note,
}: {
  label: string;
  value: string | number;
  note?: string;
}) {
  return (
    <div className="card stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {note ? <span className="stat-note">{note}</span> : null}
    </div>
  );
}
