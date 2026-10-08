import { useState } from "react";

import { describeError, type AiAnchor, type AiRun, type AiStatus } from "../api/client";
import { fetchAiPolicy, fetchAiStatus, listAiRuns, startAiRun } from "../api/endpoints";
import { formatRelative } from "../lib/format";
import { plural } from "../lib/labels";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";
import { Alert } from "./Common";
import { FileLocation } from "./FileLocation";
import { Icon, type IconName } from "./Icon";
import { statusLabel } from "./Status";

type Tone = "ok" | "warn" | "bad";

/** How far an AI statement was checked against the code (never model self-assessment). */
const EVIDENCE: Record<string, { tone: Tone; icon: IconName; label: string; help: string }> = {
  verified_anchor: {
    tone: "ok",
    icon: "check",
    label: "Checked against your code",
    help: "Every line it cites exists in this upload with the quoted code.",
  },
  hypothesis: {
    tone: "warn",
    icon: "info",
    label: "Not verified",
    help: "Some or all of it could not be matched to your code. Treat it as a lead to check.",
  },
  rejected: {
    tone: "bad",
    icon: "x",
    label: "References did not match",
    help: "None of the code it cites matches this upload.",
  },
};

const ANCHOR_STATUS: Record<string, string> = {
  verified: "Matches the code",
  unquoted: "Location only",
  quote_mismatch: "Quoted code is not there",
  bad_range: "These lines do not exist",
  unknown_path: "File is not in this upload",
};

export function EvidenceBadge({ evidence }: { evidence: string }) {
  const known = EVIDENCE[evidence] ?? EVIDENCE.hypothesis;
  if (!known) return null;
  return (
    <span className={`badge badge-${known.tone}`} title={known.help} data-evidence={evidence}>
      <Icon name={known.icon} size={13} />
      {known.label}
    </span>
  );
}

/** The code lines an answer or finding relies on, each with its verification result. */
export function AnchorList({ anchors }: { anchors: AiAnchor[] }) {
  if (anchors.length === 0) return null;
  return (
    <ul className="anchor-list" data-testid="ai-anchors">
      {anchors.map((anchor, index) => {
        const verified = anchor.status === "verified";
        return (
          <li key={`${anchor.path}-${String(anchor.start_line)}-${String(index)}`}>
            <div className="row row-between">
              <FileLocation path={anchor.path} line={anchor.start_line} endLine={anchor.end_line} />
              <span
                className={`anchor-status small ${verified ? "secondary" : "anchor-warn"}`}
                data-anchor-status={anchor.status}
              >
                <Icon name={verified ? "check" : "alert"} size={13} />{" "}
                {ANCHOR_STATUS[anchor.status] ?? anchor.status}
              </span>
            </div>
            {anchor.quote ? <pre className="ai-quote">{anchor.quote}</pre> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function providerName(status: Pick<AiStatus, "provider">): string {
  if (status.provider === "anthropic") return "Anthropic (Claude)";
  if (status.provider === "openai_compatible") return "the configured AI service";
  return "an AI service";
}

/** Short, human title of a run for lists and headings. */
export function runTitle(run: AiRun): string {
  if (run.kind === "question" && run.question) return run.question;
  if (run.kind === "file_review") {
    const paths = run.target_paths ?? [];
    return paths.length === 1 && paths[0]
      ? `Review of ${paths[0].split("/").pop() ?? paths[0]}`
      : `Review of ${plural(paths.length, "file")}`;
  }
  if (run.kind === "fix") {
    const path = run.target_paths?.[0] ?? "";
    return `Fix suggestions for ${path.split("/").pop() ?? path}`;
  }
  return "Second opinion on a finding";
}

const ERRORS: Record<string, string> = {
  workflow_unavailable: "The review service was not running, so nothing was sent. Try again.",
  interrupted: "The run was interrupted when the service restarted. Start it again.",
  ai_policy_disabled: "AI review was switched off for this project before the run started.",
  ai_unavailable: "AI review is not set up on this server.",
  monthly_token_limit: "This month's AI limit is used up.",
  monthly_cost_limit: "This month's AI spending limit is used up.",
  no_result: "The AI did not give a usable answer within its limits.",
  target_missing: "The code or finding this run was about no longer exists.",
};

export function runError(run: AiRun): string | null {
  if (!run.error_code) return null;
  if (run.error_code.startsWith("provider_")) {
    return `The AI service returned an error: ${run.error_message ?? run.error_code}`;
  }
  return ERRORS[run.error_code] ?? run.error_message ?? run.error_code;
}

export function isTerminalRun(state: string): boolean {
  return ["SUCCEEDED", "PARTIAL", "FAILED", "CANCELED", "BUDGET_EXHAUSTED"].includes(state);
}

/** Plain-language verdicts of a finding review (the model's view, never the evidence class). */
export const VERDICTS: Record<string, { tone: "ok" | "warn" | "neutral"; label: string }> = {
  confirmed: { tone: "warn", label: "Real problem" },
  likely_false_positive: { tone: "ok", label: "Likely not a problem" },
  uncertain: { tone: "neutral", label: "Not sure" },
};

function runOutcome(run: AiRun): string {
  const verdict = run.answer?.assessment?.verdict;
  if (verdict) return VERDICTS[verdict]?.label ?? verdict;
  return statusLabel(run.state);
}

/** On a finding: earlier AI second opinions, and asking for a new one when AI is set up. */
export function AiFindingCheck({ projectId, findingId }: { projectId: string; findingId: string }) {
  const status = useAsync((signal) => fetchAiStatus(signal), []);
  const policy = useAsync((signal) => fetchAiPolicy(projectId, signal), [projectId]);
  const earlier = useAsync(
    (signal) => listAiRuns(projectId, signal, findingId),
    [projectId, findingId],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const runs = earlier.data ?? [];
  const canAsk = status.data?.available === true && policy.data !== null;
  if (!canAsk && runs.length === 0) return null;
  return (
    <section className="card stack" aria-labelledby="ai-check-title" data-testid="ai-check">
      <div className="stack stack-xs">
        <h2 id="ai-check-title" className="card-title">
          <Icon name="sparkles" size={16} /> AI second opinion
        </h2>
        {canAsk ? (
          <p className="card-sub">
            {policy.data?.enabled
              ? "AI reads the code around this finding and says whether it is a real problem, citing the lines it relies on."
              : "AI review is switched off for this project."}
          </p>
        ) : null}
      </div>
      {canAsk && policy.data?.enabled ? (
        <div className="row">
          <button
            type="button"
            className="btn btn-ghost"
            disabled={busy}
            onClick={() => {
              setBusy(true);
              setError(null);
              startAiRun(projectId, { kind: "finding_review", finding_id: findingId }).then(
                (run) => {
                  navigate(`#/ai-runs/${run.id}`);
                },
                (caught: unknown) => {
                  setError(describeError(caught));
                  setBusy(false);
                },
              );
            }}
          >
            <Icon name="sparkles" size={15} /> {busy ? "Starting…" : "Ask AI to check this"}
          </button>
        </div>
      ) : null}
      {canAsk && !policy.data?.enabled ? (
        <a href={`#/projects/${projectId}?tab=ai`} className="small">
          AI review settings
        </a>
      ) : null}
      {runs.length > 0 ? (
        <ul className="stack stack-sm plain-list" data-testid="ai-check-history">
          {runs.map((run) => (
            <li key={run.id} className="row row-between">
              <a href={`#/ai-runs/${run.id}`} className="small">
                {runOutcome(run)}
              </a>
              <span className="small muted">{formatRelative(run.created_at)}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </section>
  );
}
