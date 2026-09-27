import { useState } from "react";

import type { Finding } from "../api/client";
import { fetchFinding } from "../api/endpoints";
import { CodeView } from "../components/CodeView";
import { Alert, Loading, PageHeader } from "../components/Common";
import { Icon } from "../components/Icon";
import { IssuePanel } from "../components/IssuePanel";
import { SeverityChip } from "../components/Severity";
import { StatusBadge } from "../components/Status";
import { shortHash, titleCase } from "../lib/format";
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
  ["package", "Package"],
  ["installed_version", "Installed"],
  ["fixed_version", "Fixed in"],
  ["vulnerability_id", "Advisory"],
  ["status", "Advisory status"],
  ["purl", "Package URL"],
];

function formatDetail(key: string, value: unknown): string {
  if (value === null || value === undefined)
    return key === "fixed_version" ? "no fix published" : "—";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return JSON.stringify(value);
}

function DependencyCard({ details }: { details: Record<string, unknown> }) {
  return (
    <section className="card stack" aria-labelledby="dep-title" data-testid="dependency-card">
      <h2 id="dep-title" className="card-title">
        <Icon name="projects" size={16} /> Dependency
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
        Declared in a lockfile/manifest of this snapshot; matched offline against the Trivy
        vulnerability database. Reachability is not assessed.
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
              <Icon name="arrow" size={12} style={{ transform: "rotate(180deg)" }} /> Back to scan
            </a>
          </>
        }
        title={finding.title}
        sub={finding.message}
        actions={
          <div className="row">
            <SeverityChip severity={finding.severity} />
            <span className="badge badge-neutral">{titleCase(finding.category)}</span>
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
                The engine reported this against the dependency, not a source line; no line is
                invented.
              </Alert>
            ) : (
              <Alert tone="info">Source content is not stored for this file.</Alert>
            )}
            <p className="hint">{detail.data.evidence_note}</p>
          </section>
          {dependency ? <DependencyCard details={details} /> : null}
          {related.length > 0 ? (
            <section className="card stack" aria-labelledby="related-title">
              <h2 id="related-title" className="card-title">
                <Icon name="branch" size={16} /> Also reported by
              </h2>
              <p className="small secondary" style={{ margin: 0 }}>
                Same rule family at the same location from another engine. Each observation is kept
                with its own provenance.
              </p>
              <ul className="stack" style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {related.map((other) => (
                  <li key={other.id} className="row">
                    <SeverityChip severity={other.severity} />
                    <span className="badge badge-neutral">{other.engine}</span>
                    <a href={`#/findings/${other.id}`} className="mono small">
                      {other.rule_id}
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
          <section className="card stack" aria-labelledby="guidance-title">
            <h2 id="guidance-title" className="card-title">
              <Icon name="sparkles" size={16} /> Why it matters
            </h2>
            <p style={{ margin: 0 }}>{rule.explanation}</p>
            <h3 className="card-title" style={{ fontSize: "0.92rem" }}>
              Recommendation
            </h3>
            <p style={{ margin: 0 }} data-testid="recommendation">
              {rule.recommendation}
            </p>
            <h3 className="card-title" style={{ fontSize: "0.92rem" }}>
              Severity rationale
            </h3>
            <p className="secondary" style={{ margin: 0 }}>
              {rule.severity_rationale}
            </p>
            {rule.url ? (
              <a href={rule.url} target="_blank" rel="noopener noreferrer">
                {fromDatabase ? "Advisory ↗" : "Rule documentation ↗"}
              </a>
            ) : null}
            {!rule.in_catalog ? (
              <Alert tone="warn">
                This rule is not in the platform catalog; guidance is generic.
              </Alert>
            ) : null}
            <p className="hint">
              {fromDatabase
                ? "Guidance from the offline Trivy vulnerability database — no AI was used."
                : "Static guidance from the platform rule catalog — no AI was used."}
            </p>
          </section>
          <section className="card" aria-labelledby="evidence-title">
            <h2 id="evidence-title" className="card-title" style={{ marginBottom: 12 }}>
              Evidence
            </h2>
            <dl className="kv">
              <dt>Engine</dt>
              <dd className="mono">
                {finding.engine} {finding.engine_version}
              </dd>
              <dt>Rule</dt>
              <dd className="mono">{finding.rule_id}</dd>
              <dt>Ruleset</dt>
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
            </dl>
          </section>
        </div>
      </div>
    </>
  );
}
