import { useState } from "react";

import { describeError, type IssueDetail, type IssueEvent } from "../api/client";
import { fetchIssue, triageIssue } from "../api/endpoints";
import { formatDate, formatRelative, titleCase } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import { Alert, Loading } from "./Common";
import { Icon } from "./Icon";
import { StatusBadge } from "./Status";

const TRIAGE = ["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE"] as const;
type TriageStatus = (typeof TRIAGE)[number];

function defaultExpiry(): string {
  const date = new Date(Date.now() + 90 * 24 * 3600 * 1000);
  return date.toISOString().slice(0, 10);
}

function describeEvent(event: IssueEvent): string {
  const changes = Object.entries(event.changes).map(([field, value]) => {
    const pair = Array.isArray(value) ? value : [null, value];
    const from = pair[0] === null || pair[0] === undefined ? "—" : String(pair[0]);
    const to = pair[1] === null || pair[1] === undefined ? "—" : String(pair[1]);
    return `${titleCase(field)}: ${from} → ${to}`;
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
              {titleCase(value)}
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
          Exception expires (at most one year)
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
          <Icon name="check" size={15} /> Save triage
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
      <div className="card-head" style={{ marginBottom: 0 }}>
        <h2 id="issue-title" className="card-title">
          <Icon name="shield" size={16} /> Issue
        </h2>
        <div className="row">
          <StatusBadge state={issue.status} />
          <StatusBadge state={issue.recheck_state} />
        </div>
      </div>
      <p className="small secondary" style={{ margin: 0 }} data-testid="recheck-reason">
        {issue.recheck_reason ?? "Not yet rechecked."}
      </p>
      {issue.exception_expired ? (
        <Alert tone="warn">The accepted-risk exception has expired.</Alert>
      ) : null}
      {locked ? (
        <Alert tone="info">
          {issue.status === "RESOLVED"
            ? "Resolved by a verified absence in a compatible scan. It reopens automatically if reported again."
            : "A fix proposal owns this issue's status."}
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
          Saved (version {issue.version})
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
            <dt>Expires</dt>
            <dd>{formatDate(issue.exception_expires_at)}</dd>
          </>
        ) : null}
      </dl>
      <h3 className="card-title" style={{ fontSize: "0.92rem" }}>
        History
      </h3>
      <ol className="timeline" aria-label="Issue history">
        {detail.events.map((event) => (
          <li key={event.id}>
            <div>
              <div className="small">
                <strong>{titleCase(event.kind)}</strong>{" "}
                <span className="muted">
                  {event.actor_kind === "system" ? "by scan" : "by user"} ·{" "}
                  {formatRelative(event.created_at)}
                </span>
              </div>
              <div className="small secondary">{describeEvent(event)}</div>
              {event.reason ? <div className="small muted">{event.reason}</div> : null}
            </div>
          </li>
        ))}
      </ol>
    </section>
  );
}
