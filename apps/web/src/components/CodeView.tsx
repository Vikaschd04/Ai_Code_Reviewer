import type { FileContent } from "../api/client";

/** Source excerpt rendered as text (never HTML) with line numbers and the flagged span marked. */
export function CodeView({
  content,
  from,
  to,
}: {
  content: FileContent;
  from?: number;
  to?: number;
}) {
  return (
    <div>
      <div className="code" role="region" aria-label={`Source of ${content.path}`} tabIndex={0}>
        {content.lines.map((line, index) => {
          const number = content.start_line + index;
          const hit = from !== undefined && to !== undefined && number >= from && number <= to;
          return (
            <div
              key={number}
              className={`code-line${hit ? " hit" : ""}`}
              data-line={number}
              data-hit={hit || undefined}
            >
              <span className="code-no">{number}</span>
              <span className="code-text">{line || " "}</span>
            </div>
          );
        })}
      </div>
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        Lines {content.start_line}–{content.end_line} of {content.total_lines}
        {content.redactions ? ` · ${content.redactions} likely secret value(s) masked` : ""}
        {content.truncated ? " · excerpt truncated" : ""}
      </p>
    </div>
  );
}
