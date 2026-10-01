import { useEffect, useState } from "react";

import { describeError, type CodeReview } from "../api/client";
import { cancelCodeReview, fetchCodeReview } from "../api/endpoints";
import { Alert, Disclosure, Loading, PageHeader } from "../components/Common";
import { FileLocation } from "../components/FileLocation";
import { Commit, REVIEW_ACTIVE, ReviewTarget } from "../components/GitHub";
import { Icon } from "../components/Icon";
import { SeverityChip } from "../components/Severity";
import { StatusBadge, StatusIcon } from "../components/Status";
import { checkName, plural } from "../lib/labels";
import { formatDate } from "../lib/format";

interface Item {
  finding_id: string;
  severity: string;
  title: string;
  path: string;
  line: number | null;
}

function items(value: unknown): Item[] {
  return Array.isArray(value) ? (value as Item[]) : [];
}

function number(result: Record<string, unknown>, key: string): number {
  const value = result[key];
  return typeof value === "number" ? value : 0;
}

function paths(changes: Record<string, unknown> | null | undefined, key: string): string[] {
  const group = changes?.[key] as { paths?: unknown } | undefined;
  return Array.isArray(group?.paths) ? group.paths.map(String) : [];
}

function total(changes: Record<string, unknown> | null | undefined, key: string): number {
  const group = changes?.[key] as { count?: unknown } | undefined;
  return typeof group?.count === "number" ? group.count : 0;
}

function title(review: CodeReview): string {
  if (review.kind === "pull_request") {
    return `Pull request #${String(review.pr_number)}${review.pr_title ? `: ${review.pr_title}` : ""}`;
  }
  return `${review.ref ?? "Default branch"} at ${review.head_sha?.slice(0, 7) ?? "its latest commit"}`;
}

function FindingList({ list, label }: { list: Item[]; label: string }) {
  return (
    <ul className="plain-list stack stack-sm" aria-label={label}>
      {list.map((item) => (
        <li key={item.finding_id} className="row row-between">
          <span className="stack stack-xs">
            <a href={`#/findings/${item.finding_id}`}>{item.title}</a>
            <FileLocation path={item.path} line={item.line} />
          </span>
          <SeverityChip severity={item.severity} />
        </li>
      ))}
    </ul>
  );
}

function Changes({ review }: { review: CodeReview }) {
  const changes = review.changes;
  if (!changes) {
    return (
      <p className="small secondary">
        This is the first review of this branch, so there is nothing to compare with yet.
      </p>
    );
  }
  const renamed =
    (changes.renamed as { pairs?: { from: string; to: string }[] } | undefined)?.pairs ?? [];
  const groups: { key: string; label: string }[] = [
    { key: "added", label: "Added" },
    { key: "modified", label: "Changed" },
    { key: "removed", label: "Removed" },
  ];
  const configuration = paths(changes, "configuration");
  const impacted = paths(changes, "impacted");
  return (
    <div className="stack stack-sm" data-testid="review-changes">
      <p className="small">
        {groups.map((g) => `${String(total(changes, g.key))} ${g.label.toLowerCase()}`).join(" · ")}
        {` · ${String(renamed.length)} renamed`}
      </p>
      {configuration.length > 0 ? (
        <Alert tone="info">
          Configuration changed ({configuration.join(", ")}). Checks that read project settings and
          dependencies looked at every file, not only the changed ones.
        </Alert>
      ) : null}
      <Disclosure summary="Changed files">
        <div className="stack stack-sm">
          {groups.map((g) =>
            paths(changes, g.key).length > 0 ? (
              <div key={g.key} className="stack stack-xs">
                <strong className="small">{g.label}</strong>
                <ul className="plain-list mono small">
                  {paths(changes, g.key).map((path) => (
                    <li key={path}>{path}</li>
                  ))}
                </ul>
              </div>
            ) : null,
          )}
          {renamed.length > 0 ? (
            <div className="stack stack-xs">
              <strong className="small">Renamed</strong>
              <ul className="plain-list mono small">
                {renamed.map((pair) => (
                  <li key={pair.to}>
                    {pair.from} → {pair.to}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {impacted.length > 0 ? (
            <div className="stack stack-xs">
              <strong className="small">Unchanged files that depend on the changes</strong>
              <ul className="plain-list mono small">
                {impacted.map((path) => (
                  <li key={path}>{path}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      </Disclosure>
    </div>
  );
}

/** One review of a GitHub branch or pull request: what is new, what changed, what was posted. */
export function ReviewPage({ reviewId }: { reviewId: string }) {
  const [review, setReview] = useState<CodeReview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const active = review ? REVIEW_ACTIVE.has(review.state) : false;

  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    const load = () => {
      fetchCodeReview(reviewId, controller.signal).then(
        (next) => {
          setReview(next);
          if (REVIEW_ACTIVE.has(next.state)) timer = window.setTimeout(load, 1500);
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
  }, [reviewId, active]);

  if (error) return <Alert tone="bad">{error}</Alert>;
  if (!review) return <Loading lines={6} />;
  const result = review.result ?? {};
  const finished = review.state === "SUCCEEDED" || review.state === "PARTIAL";
  const fresh = items(result.new_items);
  const fixed = items(result.fixed_items);
  const incomplete = Array.isArray(result.incomplete) ? result.incomplete.map(String) : [];
  const compared = typeof result.compared_with === "string" ? result.compared_with : null;
  return (
    <>
      <PageHeader
        eyebrow={
          <a href={`#/projects/${review.project_id}?tab=github`}>
            <Icon name="arrowLeft" size={14} /> Back to the project
          </a>
        }
        title={title(review)}
        sub={
          compared
            ? `Compared with ${compared}.`
            : review.repository
              ? `Repository ${review.repository}`
              : undefined
        }
        actions={
          <>
            <StatusBadge state={review.state} />
            {review.pr_url ? (
              <a
                className="btn btn-ghost btn-sm"
                href={review.pr_url}
                target="_blank"
                rel="noreferrer"
              >
                <Icon name="external" size={14} /> Open on GitHub
              </a>
            ) : null}
            {active ? (
              <button
                type="button"
                className="btn btn-ghost btn-sm"
                disabled={stopping}
                onClick={() => {
                  setStopping(true);
                  cancelCodeReview(review.id).then(setReview, (caught: unknown) => {
                    setError(describeError(caught));
                  });
                }}
              >
                <Icon name="x" size={14} /> Stop
              </button>
            ) : null}
          </>
        }
      />
      {active ? (
        <p className="row small" data-testid="review-progress">
          <span className="pulse-dot" />
          Working on it: getting the exact commit, checking it and comparing the results.
        </p>
      ) : null}
      {review.error_message && !finished ? (
        <Alert tone={review.state === "FAILED" ? "bad" : "info"}>{review.error_message}</Alert>
      ) : null}
      {finished ? (
        <>
          <div
            className="tiles tiles-4"
            role="group"
            aria-label="Results"
            data-testid="review-tiles"
          >
            {[
              { key: "new", state: "OPEN", label: "New", hint: "introduced here" },
              {
                key: "fixed",
                state: "VERIFIED_ABSENT",
                label: "Fixed",
                hint: "no longer reported",
              },
              {
                key: "unchanged",
                state: "VERIFIED_PRESENT",
                label: "Still present",
                hint: "already there before",
              },
              {
                key: "not_rechecked",
                state: "NOT_RECHECKED",
                label: "Not rechecked",
                hint: "file removed or check did not run",
              },
            ].map((tile) => (
              <div key={tile.key} className="tile" data-testid={`review-tile-${tile.key}`}>
                <StatusBadge state={tile.state} label={tile.label} />
                <span className="tile-value">{number(result, tile.key)}</span>
                <span className="tile-label">{tile.hint}</span>
              </div>
            ))}
          </div>
          {incomplete.length > 0 ? (
            <Alert tone="warn">
              Not every check finished ({incomplete.map(checkName).join(", ")}), so problems in
              their area may be missing. Nothing was marked fixed because of it.
            </Alert>
          ) : null}
          <div className="split">
            <div className="stack">
              <section className="card stack" aria-labelledby="new-title" data-testid="review-new">
                <h2 id="new-title" className="card-title">
                  <Icon name="alert" size={16} />{" "}
                  {fresh.length === 0
                    ? "No new problems"
                    : plural(number(result, "new"), "new problem")}
                </h2>
                {fresh.length > 0 ? <FindingList list={fresh} label="New problems" /> : null}
                {number(result, "new") > fresh.length ? (
                  <p className="small muted">
                    Showing the {fresh.length} most severe.{" "}
                    {review.head_scan_id ? (
                      <a href={`#/scans/${review.head_scan_id}`}>See every finding</a>
                    ) : null}
                  </p>
                ) : null}
              </section>
              {fixed.length > 0 ? (
                <section className="card stack" aria-labelledby="fixed-title">
                  <h2 id="fixed-title" className="card-title">
                    <Icon name="check" size={16} /> Fixed here
                  </h2>
                  <FindingList list={fixed} label="Fixed problems" />
                </section>
              ) : null}
            </div>
            <div className="stack">
              <section className="card stack" aria-labelledby="changes-title">
                <h2 id="changes-title" className="card-title">
                  <Icon name="file" size={16} /> What changed
                </h2>
                <Changes review={review} />
              </section>
              <section className="card stack" aria-labelledby="posted-title">
                <h2 id="posted-title" className="card-title">
                  <Icon name="branch" size={16} /> On GitHub
                </h2>
                <p className="row small" data-testid="review-publish">
                  <StatusIcon
                    state={
                      review.publish_state === "published"
                        ? "SUCCEEDED"
                        : review.publish_state === "failed"
                          ? "FAILED"
                          : "NOT_APPLICABLE"
                    }
                  />
                  {review.publish_state === "published"
                    ? "A check and a summary comment were posted."
                    : review.publish_state === "failed"
                      ? `Could not post to GitHub: ${review.publish_error ?? "unknown error"}`
                      : "Nothing was posted: the project does not post results to GitHub."}
                </p>
                {review.head_scan_id ? (
                  <a className="small" href={`#/scans/${review.head_scan_id}`}>
                    Open every finding of this review
                  </a>
                ) : null}
              </section>
            </div>
          </div>
        </>
      ) : null}
      <Disclosure testId="review-technical">
        <dl className="kv">
          <dt>Repository</dt>
          <dd className="mono">{review.repository ?? "—"}</dd>
          <dt>What</dt>
          <dd>
            <ReviewTarget review={review} />
          </dd>
          <dt>Commit</dt>
          <dd>
            <Commit sha={review.head_sha} />
          </dd>
          {review.merge_base_sha ? (
            <>
              <dt>Merge base</dt>
              <dd>
                <Commit sha={review.merge_base_sha} /> ({review.base_ref})
              </dd>
            </>
          ) : review.base_sha ? (
            <>
              <dt>Previous review</dt>
              <dd>
                <Commit sha={review.base_sha} />
              </dd>
            </>
          ) : null}
          <dt>Started by</dt>
          <dd>{review.trigger}</dd>
          <dt>Results reused</dt>
          <dd>{review.full ? "No (full review)" : "Yes, for unchanged files"}</dd>
          {review.head_snapshot_id ? (
            <>
              <dt>Upload</dt>
              <dd>
                <a href={`#/snapshots/${review.head_snapshot_id}`}>Captured commit</a>
              </dd>
            </>
          ) : null}
          {review.superseded_by ? (
            <>
              <dt>Replaced by</dt>
              <dd>
                <a href={`#/reviews/${review.superseded_by}`}>Newer review</a>
              </dd>
            </>
          ) : null}
          <dt>Created</dt>
          <dd>{formatDate(review.created_at)}</dd>
        </dl>
      </Disclosure>
    </>
  );
}
