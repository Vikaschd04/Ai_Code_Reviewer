import { useEffect, useState } from "react";

import { describeError, type AiFinding, type AiRun } from "../api/client";
import { cancelAiRun, fetchAiRun } from "../api/endpoints";
import { AnchorList, EvidenceBadge, isTerminalRun, runError, runTitle } from "../components/Ai";
import { Alert, Disclosure, Loading, PageHeader } from "../components/Common";
import { Icon } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { StatusBadge } from "../components/Status";
import { formatDate, formatNumber } from "../lib/format";
import { categoryLabel, plural } from "../lib/labels";

const VERDICTS: Record<string, { tone: "ok" | "warn" | "neutral"; label: string }> = {
  confirmed: { tone: "warn", label: "Real problem" },
  likely_false_positive: { tone: "ok", label: "Likely not a problem" },
  uncertain: { tone: "neutral", label: "Not sure" },
};

const STEP_LABELS: Record<string, string> = {
  plan: "Prepared the request",
  model: "Asked the AI",
  tool: "Looked up code",
  submit: "Checked the result",
};

const KIND_SUB: Record<string, string> = {
  question: "Question about this project's code",
  file_review: "AI review of selected files",
  finding_review: "AI second opinion on a finding",
};

function Paragraphs({ text }: { text: string }) {
  return <div className="ai-text">{text}</div>;
}

function Running({ run, onCancel }: { run: AiRun; onCancel: () => void }) {
  const steps = run.steps.filter((step) => step.action === "tool").length;
  return (
    <section className="card stack" aria-live="polite" data-testid="ai-running">
      <div className="row">
        <span className="pulse-dot" />
        <strong>
          {run.state === "QUEUED" ? "Waiting to start…" : "The AI is reading your code…"}
        </strong>
      </div>
      <p className="small secondary">
        {run.cancel_requested_at
          ? "Stopping…"
          : steps > 0
            ? `${plural(steps, "lookup")} so far. This usually takes under a minute.`
            : "This usually takes under a minute."}
      </p>
      {!run.cancel_requested_at ? (
        <div className="row">
          <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>
            <Icon name="x" size={14} /> Stop
          </button>
        </div>
      ) : null}
    </section>
  );
}

function AiFindingCard({ finding }: { finding: AiFinding }) {
  return (
    <li className="card stack" data-testid="ai-finding">
      <div className="row row-between">
        <div className="row">
          <SeverityChip severity={finding.severity} />
          <span className="badge badge-neutral">{categoryLabel(finding.category)}</span>
        </div>
        <EvidenceBadge evidence={finding.evidence_class} />
      </div>
      <h3 className="subheading">{finding.title}</h3>
      <dl className="details">
        <div>
          <dt>What can go wrong</dt>
          <dd>{finding.impact}</dd>
        </div>
        <div>
          <dt>When it happens</dt>
          <dd>{finding.triggering_conditions}</dd>
        </div>
        <div>
          <dt>How to fix</dt>
          <dd>{finding.recommendation}</dd>
        </div>
        {finding.validation_needed ? (
          <div>
            <dt>To confirm</dt>
            <dd>{finding.validation_needed}</dd>
          </div>
        ) : null}
        <div>
          <dt>AI confidence</dt>
          <dd>
            {finding.confidence} · {finding.severity_rationale}
          </dd>
        </div>
      </dl>
      <AnchorList anchors={finding.anchors} />
      {finding.uncertainty ? <p className="small muted">{finding.uncertainty}</p> : null}
    </li>
  );
}

function Findings({ findings }: { findings: AiFinding[] }) {
  const kept = findings.filter((f) => f.evidence_class !== "rejected");
  const discarded = findings.filter((f) => f.evidence_class === "rejected");
  return (
    <section className="stack" aria-labelledby="ai-findings-title">
      <h2 id="ai-findings-title" className="section-title">
        {kept.length > 0 ? `Problems found (${String(kept.length)})` : "No problems found"}
      </h2>
      {kept.length > 0 ? (
        <ul className="stack plain-list">
          {kept.map((finding) => (
            <AiFindingCard key={finding.id} finding={finding} />
          ))}
        </ul>
      ) : null}
      {discarded.length > 0 ? (
        <Disclosure
          summary={`${plural(discarded.length, "suggestion")} discarded: the code it cites does not match`}
          testId="ai-discarded"
        >
          <ul className="stack plain-list">
            {discarded.map((finding) => (
              <AiFindingCard key={finding.id} finding={finding} />
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </section>
  );
}

function Answer({ run }: { run: AiRun }) {
  const answer = run.answer;
  if (!answer) return null;
  if (answer.type === "review") {
    const assessment = answer.assessment;
    const verdict = assessment ? VERDICTS[assessment.verdict] : undefined;
    return (
      <>
        {assessment ? (
          <section className="card stack" aria-labelledby="ai-verdict-title">
            <div className="card-head">
              <h2 id="ai-verdict-title" className="card-title">
                Verdict
              </h2>
              <div className="row">
                {verdict ? (
                  <span className={`badge badge-${verdict.tone}`} data-testid="ai-verdict">
                    {verdict.label}
                  </span>
                ) : null}
                <EvidenceBadge evidence={assessment.evidence_class} />
              </div>
            </div>
            <Paragraphs text={assessment.explanation} />
            <AnchorList anchors={assessment.anchors} />
          </section>
        ) : null}
        <section className="card stack" aria-labelledby="ai-summary-title">
          <h2 id="ai-summary-title" className="card-title">
            Summary
          </h2>
          <Paragraphs text={answer.text} />
          {answer.reviewed_paths.length > 0 ? (
            <p className="small muted">
              Read: <span className="mono">{answer.reviewed_paths.join(", ")}</span>
            </p>
          ) : null}
        </section>
        {run.kind !== "finding_review" || run.findings.length > 0 ? (
          <Findings findings={run.findings} />
        ) : null}
      </>
    );
  }
  return (
    <section className="card stack" aria-labelledby="ai-answer-title" data-testid="ai-answer">
      <div className="card-head">
        <h2 id="ai-answer-title" className="card-title">
          <Icon name="sparkles" size={16} /> Answer
        </h2>
        {answer.evidence_class ? <EvidenceBadge evidence={answer.evidence_class} /> : null}
      </div>
      {answer.abstained ? (
        <Alert tone="info">The AI could not answer this from the uploaded code.</Alert>
      ) : null}
      <Paragraphs text={answer.text} />
      {answer.uncertainty ? (
        <p className="small secondary">
          <strong>Unsure about:</strong> {answer.uncertainty}
        </p>
      ) : null}
      {answer.inferred_intent ? (
        <p className="small secondary">
          <strong>Assumed intent:</strong> {answer.inferred_intent}
        </p>
      ) : null}
      {answer.citations.length > 0 ? (
        <div className="stack stack-sm">
          <h3 className="subheading">Based on these lines</h3>
          <AnchorList anchors={answer.citations} />
        </div>
      ) : null}
    </section>
  );
}

function Technical({ run }: { run: AiRun }) {
  const usage = run.usage;
  return (
    <Disclosure testId="ai-technical">
      <dl className="kv">
        <dt>AI service</dt>
        <dd>
          {run.provider} · <span className="mono">{run.model}</span>
        </dd>
        <dt>Instructions version</dt>
        <dd className="mono">{run.prompt_version}</dd>
        <dt>Started</dt>
        <dd>{formatDate(run.started_at ?? run.created_at)}</dd>
        {usage ? (
          <>
            <dt>Model calls</dt>
            <dd>{usage.calls}</dd>
            <dt>Tokens</dt>
            <dd>
              {formatNumber(usage.input_tokens)} in · {formatNumber(usage.output_tokens)} out
              {usage.usage_reported ? "" : " (estimated: the service did not report usage)"}
            </dd>
            <dt>Cost</dt>
            <dd>
              {usage.cost_usd === null || usage.cost_usd === undefined
                ? "Unknown (no prices configured)"
                : `$${usage.cost_usd.toFixed(4)}`}
            </dd>
            <dt>File sections read</dt>
            <dd>{usage.excerpts}</dd>
          </>
        ) : null}
      </dl>
      {run.steps.length > 0 ? (
        <ol className="timeline" aria-label="What the AI did">
          {run.steps.map((step) => (
            <li key={step.n}>
              <div>
                <div className="small">
                  <strong>{STEP_LABELS[step.action] ?? step.action}</strong>
                </div>
                <div className="small secondary wrap-anywhere">{step.detail}</div>
                <div className="small muted wrap-anywhere">{step.outcome}</div>
              </div>
            </li>
          ))}
        </ol>
      ) : null}
    </Disclosure>
  );
}

export function AiRunPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<AiRun | null>(null);
  const [error, setError] = useState<string | null>(null);
  const terminal = run ? isTerminalRun(run.state) : false;

  useEffect(() => {
    if (terminal) return;
    const controller = new AbortController();
    let timer = 0;
    const load = () => {
      fetchAiRun(runId, controller.signal).then(
        (next) => {
          setRun(next);
          if (!isTerminalRun(next.state)) timer = window.setTimeout(load, 1500);
        },
        (caught: unknown) => {
          if (!controller.signal.aborted) setError(describeError(caught));
        },
      );
    };
    load();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [runId, terminal]);

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!run) return <Loading lines={5} />;
  const problem = runError(run);
  return (
    <>
      <PageHeader
        eyebrow={
          <a href={`#/projects/${run.project_id}?tab=ai`}>
            <Icon name="arrowLeft" size={14} /> Back to AI review
          </a>
        }
        title={runTitle(run)}
        sub={KIND_SUB[run.kind]}
        actions={<StatusBadge state={run.state} />}
      />
      <div className="split">
        <div className="stack">
          {!terminal ? (
            <Running
              run={run}
              onCancel={() => {
                cancelAiRun(run.id).then(setRun, (caught: unknown) => {
                  setError(describeError(caught));
                });
              }}
            />
          ) : null}
          {problem ? (
            <Alert tone={run.state === "CANCELED" ? "info" : "bad"}>{problem}</Alert>
          ) : null}
          {run.state === "CANCELED" && !problem ? (
            <Alert tone="info">This run was stopped.</Alert>
          ) : null}
          {run.state === "BUDGET_EXHAUSTED" && !run.answer ? (
            <Alert tone="warn">
              The AI reached its limit for one run before finishing. Ask a narrower question or
              choose fewer files.
            </Alert>
          ) : null}
          <Answer run={run} />
        </div>
        <div className="stack">
          {terminal ? (
            <section className="card stack" aria-labelledby="ai-trust-title">
              <h2 id="ai-trust-title" className="card-title">
                <Icon name="shield" size={16} /> How to read this
              </h2>
              <ul className="stack stack-sm plain-list small secondary">
                <li>
                  <EvidenceBadge evidence="verified_anchor" /> means every cited line exists in your
                  upload with the quoted code, not that the conclusion is right.
                </li>
                {run.limitations.map((text) => (
                  <li key={text}>{text}</li>
                ))}
              </ul>
            </section>
          ) : null}
          <Technical run={run} />
        </div>
      </div>
    </>
  );
}
