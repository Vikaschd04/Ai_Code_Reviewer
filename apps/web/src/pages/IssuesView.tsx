import { useState } from "react";

import { describeError, type Issue } from "../api/client";
import { fetchIssue, listIssues } from "../api/endpoints";
import { Alert, Empty, Loading } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { Icon } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { StatusBadge, statusLabel } from "../components/Status";
import { formatRelative } from "../lib/format";
import { navigate } from "../lib/router";
import { useAsync } from "../lib/useAsync";

const STATUSES = ["OPEN", "TRIAGED", "ACCEPTED_RISK", "FALSE_POSITIVE", "FIX_PROPOSED", "RESOLVED"];
const RECHECKS = [
  "VERIFIED_PRESENT",
  "VERIFIED_ABSENT",
  "NOT_RECHECKED",
  "UNKNOWN",
  "RULE_OBSOLETE",
];

/** Durable issues across scans: triage state and what the newest compatible scan proved. */
export function IssuesView({ projectId }: { projectId: string }) {
  const [status, setStatus] = useState("");
  const [recheck, setRecheck] = useState("");
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<Issue[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const page = useAsync(
    (signal) =>
      listIssues(projectId, { status, recheck, q: query }, signal).then((result) => {
        setExtra([]);
        setCursor(result.next_cursor);
        return result;
      }),
    [projectId, status, recheck, query],
  );
  const rows = [...(page.data?.items ?? []), ...extra];
  const open = (issue: Issue) => {
    fetchIssue(issue.id).then(
      (detail) => {
        if (detail.latest_finding_id) navigate(`#/findings/${detail.latest_finding_id}`);
      },
      (caught: unknown) => {
        setError(describeError(caught));
      },
    );
  };
  return (
    <section className="card stack" aria-labelledby="issues-title" data-testid="issues">
      <div className="card-head" style={{ marginBottom: 0 }}>
        <div>
          <h2 id="issues-title" className="card-title">
            <Icon name="shield" size={16} /> Issues
          </h2>
          <p className="card-sub">
            Problems tracked across reviews. Status is your team&apos;s decision; “latest review”
            shows whether the newest review still finds it.
          </p>
        </div>
      </div>
      {page.data ? (
        <>
          <div className="tiles" role="group" aria-label="Filter by status">
            {STATUSES.filter((s) => (page.data?.by_status[s] ?? 0) > 0 || s === "OPEN").map(
              (value) => (
                <button
                  key={value}
                  type="button"
                  className="tile"
                  aria-pressed={status === value}
                  onClick={() => {
                    setStatus((current) => (current === value ? "" : value));
                  }}
                >
                  <StatusBadge state={value} />
                  <span className="tile-value">{page.data?.by_status[value] ?? 0}</span>
                </button>
              ),
            )}
          </div>
          <div className="row" role="group" aria-label="Filter by latest review">
            {RECHECKS.map((value) => (
              <button
                key={value}
                type="button"
                className="pill-toggle"
                aria-pressed={recheck === value}
                onClick={() => {
                  setRecheck((current) => (current === value ? "" : value));
                }}
              >
                {statusLabel(value)} <strong>{page.data?.by_recheck[value] ?? 0}</strong>
              </button>
            ))}
            <input
              type="search"
              aria-label="Search issues by title or path"
              placeholder="Search title or path…"
              style={{ maxWidth: 240 }}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
              }}
            />
          </div>
        </>
      ) : null}
      {page.error ? <Alert tone="bad">{page.error}</Alert> : null}
      {error ? <Alert tone="bad">{error}</Alert> : null}
      {page.loading && !page.data ? <Loading /> : null}
      {page.data && rows.length === 0 ? (
        <Empty title="No issues match">
          <p className="small">Issues appear after a review finds problems.</p>
        </Empty>
      ) : null}
      {rows.length > 0 ? (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="visually-hidden">Issues</caption>
            <thead>
              <tr>
                <th scope="col">Severity</th>
                <th scope="col">Issue</th>
                <th scope="col">Status</th>
                <th scope="col">Latest review</th>
                <th scope="col">Owner</th>
                <th scope="col">Last seen</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((issue) => (
                <tr
                  key={issue.id}
                  className="clickable"
                  data-testid="issue-row"
                  onClick={() => {
                    open(issue);
                  }}
                >
                  <td>
                    <SeverityChip severity={issue.severity} />
                  </td>
                  <th scope="row" style={{ fontWeight: 600 }}>
                    <button
                      type="button"
                      className="link-button"
                      onClick={(event) => {
                        event.stopPropagation();
                        open(issue);
                      }}
                    >
                      {issue.title}
                    </button>
                    <div className="small">
                      <FileLocation path={issue.path} />
                    </div>
                  </th>
                  <td>
                    <StatusBadge state={issue.status} />
                    {issue.exception_expired ? (
                      <div className="small muted">acceptance ended</div>
                    ) : null}
                  </td>
                  <td title={issue.recheck_reason ?? undefined}>
                    <StatusBadge state={issue.recheck_state} />
                  </td>
                  <td className="small">{issue.owner ?? "—"}</td>
                  <td className="small">{formatRelative(issue.last_seen_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {cursor ? (
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => {
            listIssues(projectId, { status, recheck, q: query, cursor }).then(
              (next) => {
                setExtra((previous) => [...previous, ...next.items]);
                setCursor(next.next_cursor);
              },
              () => {
                setCursor(null);
              },
            );
          }}
        >
          Load more
        </button>
      ) : null}
    </section>
  );
}
