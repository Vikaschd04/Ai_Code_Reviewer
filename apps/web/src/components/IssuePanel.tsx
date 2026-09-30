import { useState } from "react";

import { describeError, type IssueDetail, type IssueEvent } from "../api/client";
import { fetchIssue, triageIssue } from "../api/endpoints";
import { formatDate, formatRelative, titleCase } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { Alert, Loading } from "./Common";
import { Icon } from "./Icon";
import { StatusBadge, statusLabel } from "./Status";

const TRIAGE = ["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE"] as const;
type TriageStatus = (typeof TRIAGE)[number];

function defaultExpiry(): string {
  const date = new Date(Date.now() + 90 * 24 * 3600 * 1000);
  return date.toISOString().slice(0, 10);
}

/** Server explanations say "scan"; the product calls it a review. */
function reviewWording(text: string): string {
  return text.replace(/\bscans?\b/g, (word) => (word === "scan" ? "review" : "reviews"));
}

const FIELD_LABELS: Record<string, string> = {
  status: "Status",
  owner: "Owner",
  exception_reason: "Reason",
  exception_expires_at: "Accepted until",
  recheck_state: "Latest review",
};

function describeValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "none";
  const text =
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : JSON.stringify(value);
  if (field === "exception_expires_at") return formatDate(text);
  if (field === "status" || field === "recheck_state") return statusLabel(text);
  return text;
}

function describeEvent(event: IssueEvent): string {
  const changes = Object.entries(event.changes)
    .filter(([field]) => field !== "exception_reason" || !event.reason)
    .map(([field, value]) => {
      const pair = Array.isArray(value) ? value : [null, value];
      const label = FIELD_LABELS[field] ?? titleCase(field);
      return `${label}: ${describeValue(field, pair[0])} → ${describeValue(field, pair[1])}`;
    });
  return changes.join(" · ");
}

function TriageForm({
  issue,
  onSaved,
}: {
  issue: IssueDetail["issue"];
  onSaved: (next: IssueDetail) => void;
}) {
  const [status, setStatus] = useState<TriageStatus>(
    (TRIAGE as readonly string[]).includes(issue.status) ? (issue.status as TriageStatus) : "OPEN",
  );
  const [owner, setOwner] = useState(issue.owner ?? "");
  const [reason, setReason] = useState(issue.exception_reason ?? "");
  const [expiry, setExpiry] = useState(
    issue.exception_expires_at ? issue.exception_expires_at.slice(0, 10) : defaultExpiry(),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const needsReason = status === "ACCEPTED_RISK" || status === "FALSE_POSITIVE";

  const submit = () => {
    setBusy(true);
    setError(null);
    triageIssue(issue.id, {
      version: issue.version,
      status,
      owner,
      reason: needsReason ? reason : null,
      // End of the chosen day in the reviewer's local time zone.
      expires_at: status === "ACCEPTED_RISK" ? new Date(`${expiry}T23:59:59`).toISOString() : null,
    }).then(onSaved, (caught: unknown) => {
      setError(describeError(caught));
      setBusy(false);
    });
  };

  return (
    <form
      className="form-grid"
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <label>
        Status
        <select
          value={status}
          onChange={(event) => {
            setStatus(event.target.value as TriageStatus);
          }}
        >
          {TRIAGE.map((value) => (
            <option key={value} value={value}>
              {statusLabel(value)}
            </option>
          ))}
        </select>
      </label>
      <label>
        Owner
        <input
          value={owner}
          maxLength={200}
          placeholder="Team or person"
          onChange={(event) => {
            setOwner(event.target.value);
          }}
        />
      </label>
      {needsReason ? (
        <label>
          Reason (required)
          <textarea
            value={reason}
            rows={3}
            maxLength={2000}
            required
            onChange={(event) => {
              setReason(event.target.value);
            }}
          />
        </label>
      ) : null}
      {status === "ACCEPTED_RISK" ? (
        <label>
          Accept the risk until (at most one year)
          <input
            type="date"
            value={expiry}
            required
            onChange={(event) => {
              setExpiry(event.target.value);
            }}
          />
        </label>
      ) : null}
      <div className="row">
        <button type="submit" className="btn btn-primary" disabled={busy}>
          <Icon name="check" size={15} /> Save decision
        </button>
      </div>
      {error ? <Alert tone="bad">{error}</Alert> : null}
    </form>
  );
}

/** Triage and audit trail for the durable issue behind a finding. */
export function IssuePanel({
  issueId,
  onChange,
}: {
  issueId: string;
  onChange?: (next: IssueDetail) => void;
}) {
  const loaded = useAsync((signal) => fetchIssue(issueId, signal), [issueId]);
  const [saved, setSaved] = useState<IssueDetail | null>(null);
  const handleSaved = (next: IssueDetail) => {
    setSaved(next);
    onChange?.(next);
  };
  const detail = saved?.issue.id === issueId ? saved : loaded.data;
  if (loaded.error) return <Alert tone="bad">{loaded.error}</Alert>;
  if (!detail) return <Loading lines={4} />;
  const issue = detail.issue;
  const locked = issue.status === "RESOLVED" || issue.status === "FIX_PROPOSED";

  return (
    <section className="card stack" aria-labelledby="issue-title" data-testid="issue-panel">
      <div className="card-head">
        <h2 id="issue-title" className="card-title">
          <Icon name="shield" size={16} /> Your decision
        </h2>
        <StatusBadge state={issue.status} />
      </div>
      <div className="row small secondary" data-testid="recheck-reason">
        <span>Latest review:</span> <StatusBadge state={issue.recheck_state} />
        {issue.recheck_reason ? (
          <span className="muted">{reviewWording(issue.recheck_reason)}</span>
        ) : null}
      </div>
      {issue.exception_expired ? (
        <Alert tone="warn">The accepted-risk period has ended; decide again.</Alert>
      ) : null}
      {locked ? (
        <Alert tone="info">
          {issue.status === "RESOLVED"
            ? "Fixed: a later review checked the file and the problem is gone. It reopens automatically if it comes back."
            : "A proposed fix is handling this issue."}
        </Alert>
      ) : (
        <TriageForm
          key={`${issue.id}-${String(issue.version)}`}
          issue={issue}
          onSaved={handleSaved}
        />
      )}
      {saved?.issue.id === issueId ? (
        <span className="small" role="status">
          Saved
        </span>
      ) : null}
      <dl className="kv">
        <dt>First seen</dt>
        <dd>{formatDate(issue.created_at)}</dd>
        <dt>Last seen</dt>
        <dd>{issue.last_seen_at ? formatRelative(issue.last_seen_at) : "—"}</dd>
        <dt>Owner</dt>
        <dd>{issue.owner ?? "—"}</dd>
        {issue.exception_expires_at ? (
          <>
            <dt>Accepted until</dt>
            <dd>{formatDate(issue.exception_expires_at)}</dd>
          </>
        ) : null}
      </dl>
      <div className="stack stack-sm">
        <h3 className="subheading">History</h3>
        <ol className="timeline" aria-label="Issue history">
          {detail.events.map((event) => (
            <li key={event.id}>
              <div>
                <div className="small">
                  <strong>{titleCase(event.kind)}</strong>{" "}
                  <span className="muted">
                    {event.actor_kind === "system" ? "by a review" : "by a reviewer"} ·{" "}
                    {formatRelative(event.created_at)}
                  </span>
                </div>
                <div className="small secondary">{describeEvent(event)}</div>
                {event.reason ? <div className="small muted">{event.reason}</div> : null}
              </div>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}
