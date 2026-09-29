import { useState } from "react";

import type { Finding } from "../api/client";
import { fetchFinding } from "../api/endpoints";
import { CodeView } from "../components/CodeView";
import { Alert, Disclosure, Loading, PageHeader } from "../components/Common";
import { Icon } from "../components/Icon";
import { IssuePanel } from "../components/IssuePanel";
import { SeverityChip } from "../components/Severity";
import { StatusBadge } from "../components/Status";
import { shortHash } from "../lib/format";
import { CHECKS, categoryLabel, checkName } from "../lib/labels";
import { useAsync } from "../lib/useAsync";

/** ``path:line`` for source spans; dependency and file findings have no invented line. */
export function findingLocation(finding: Finding): string {
  if (finding.start_line === null) {
    return `${finding.path} · ${finding.anchor_kind === "dependency" ? "dependency" : "whole file"}`;
  }
  const end =
    finding.end_line !== null && finding.end_line !== finding.start_line
      ? `–${String(finding.end_line)}`
      : "";
  return `${finding.path}:${String(finding.start_line)}${end}`;
}

const DEPENDENCY_FIELDS: [string, string][] = [
  ["package", "Library"],
  ["installed_version", "Your version"],
  ["fixed_version", "Fixed in version"],
  ["vulnerability_id", "Advisory"],
];

function formatDetail(key: string, value: unknown): string {
  if (value === null || value === undefined)
    return key === "fixed_version" ? "No fix published yet" : "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function DependencyCard({ details }: { details: Record<string, unknown> }) {
  return (
    <section className="card stack" aria-labelledby="dep-title" data-testid="dependency-card">
      <h2 id="dep-title" className="card-title">
        <Icon name="box" size={16} /> Vulnerable library
      </h2>
      <dl className="dep-grid">
        {DEPENDENCY_FIELDS.map(([key, label]) => (
          <div key={key}>
            <dt>{label}</dt>
            <dd>{formatDetail(key, details[key])}</dd>
          </div>
        ))}
      </dl>
      <p className="hint">
        Declared in your dependency files and matched against a public vulnerability database.
        Whether your code actually uses the vulnerable part is not checked.
      </p>
    </section>
  );
}

export function FindingPage({ findingId }: { findingId: string }) {
  const detail = useAsync((signal) => fetchFinding(findingId, signal), [findingId]);
  const [issueStatus, setIssueStatus] = useState<string | null>(null);
  if (detail.error) return <Alert tone="bad">{detail.error}</Alert>;
  if (!detail.data) return <Loading lines={6} />;
  const { finding, rule, source } = detail.data;
  const related = detail.data.related ?? [];
  const details = finding.details ?? null;
  const dependency = finding.anchor_kind === "dependency" && details !== null;
  const fromDatabase = details !== null && "vulnerability_id" in details;

  return (
    <>
      <PageHeader
        eyebrow={
          <>
            <a href={`#/scans/${finding.scan_id}`}>
              <Icon name="arrow" size={12} style={{ transform: "rotate(180deg)" }} /> Back to review
              results
            </a>
          </>
        }
        title={finding.title}
        sub={finding.message}
        actions={
          <div className="row">
            <SeverityChip severity={finding.severity} />
            <span className="badge badge-neutral">{categoryLabel(finding.category)}</span>
            {finding.issue ? <StatusBadge state={issueStatus ?? finding.issue.status} /> : null}
          </div>
        }
      />
      <div className="split">
        <div className="stack">
          <section className="card stack" aria-labelledby="source-title">
            <div className="card-head" style={{ marginBottom: 0 }}>
              <h2 id="source-title" className="card-title mono" data-testid="finding-location">
                {findingLocation(finding)}
              </h2>
            </div>
            {source && finding.start_line !== null ? (
              <CodeView
                content={source}
                from={finding.start_line}
                to={finding.end_line ?? finding.start_line}
              />
            ) : dependency ? (
              <Alert tone="info">
                This finding is about a library your project depends on, not a line of your code.
              </Alert>
            ) : (
              <Alert tone="info">This file's content is not shown (it is not stored).</Alert>
            )}
          </section>
          <section className="card stack" aria-labelledby="guidance-title">
            <h2 id="guidance-title" className="card-title">
              <Icon name="sparkles" size={16} /> Why it matters
            </h2>
            <p style={{ margin: 0 }}>{rule.explanation}</p>
            <h3 className="card-title" style={{ fontSize: "0.92rem" }}>
              How to fix
            </h3>
            <p style={{ margin: 0 }} data-testid="recommendation">
              {rule.recommendation}
            </p>
            <p className="small muted" style={{ margin: 0 }}>
              <strong>Why this severity:</strong> {rule.severity_rationale}
            </p>
            {rule.url ? (
              <a href={rule.url} target="_blank" rel="noopener noreferrer">
                {fromDatabase ? "Read the advisory ↗" : "Learn more about this rule ↗"}
              </a>
            ) : null}
            {!rule.in_catalog ? (
              <Alert tone="warn">This rule has no detailed guidance yet.</Alert>
            ) : null}
            <p className="hint">
              {fromDatabase
                ? "Guidance from the public vulnerability database. No AI was used."
                : "Guidance from the refactorX rule catalog. No AI was used."}
            </p>
          </section>
          {dependency ? <DependencyCard details={details} /> : null}
          {related.length > 0 ? (
            <section className="card stack" aria-labelledby="related-title">
              <h2 id="related-title" className="card-title">
                <Icon name="check" size={16} /> Also found by
              </h2>
              <p className="small secondary" style={{ margin: 0 }}>
                Another check found the same problem in the same place, which makes it more certain.
              </p>
              <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {related.map((other) => (
                  <li key={other.id} className="row">
                    <SeverityChip severity={other.severity} />
                    <a href={`#/findings/${other.id}`} className="small">
                      {checkName(other.engine)}
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          ) : null}
        </div>
        <div className="stack">
          {finding.issue ? (
            <IssuePanel
              issueId={finding.issue.id}
              onChange={(next) => {
                setIssueStatus(next.issue.status);
              }}
            />
          ) : null}
          <Disclosure testId="finding-technical">
            <dl className="kv">
              <dt>Found by</dt>
              <dd>
                {checkName(finding.engine)}{" "}
                <span className="mono muted">
                  ({CHECKS[finding.engine]?.tool ?? finding.engine} {finding.engine_version})
                </span>
              </dd>
              <dt>Rule</dt>
              <dd className="mono">{finding.rule_id}</dd>
              <dt>Rule set</dt>
              <dd className="mono">{finding.ruleset ?? "—"}</dd>
              {finding.rule_family ? (
                <>
                  <dt>Rule family</dt>
                  <dd className="mono">{finding.rule_family}</dd>
                </>
              ) : null}
              <dt>Confidence</dt>
              <dd>{finding.confidence.replaceAll("_", " ")}</dd>
              <dt>Fingerprint</dt>
              <dd className="hash">{shortHash(finding.fingerprint, 24)}</dd>
              <dt>Snapshot</dt>
              <dd className="hash">{shortHash(detail.data.manifest_sha256, 24)}</dd>
              {typeof details?.purl === "string" ? (
                <>
                  <dt>Package URL</dt>
                  <dd className="mono wrap-anywhere">{details.purl}</dd>
                </>
              ) : null}
            </dl>
            <p className="hint">{detail.data.evidence_note}</p>
          </Disclosure>
        </div>
      </div>
    </>
  );
}
