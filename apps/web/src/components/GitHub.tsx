import type { CodeReview, Snapshot } from "../api/client";
import { plural } from "../lib/labels";
import { Icon } from "./Icon";

export const REVIEW_ACTIVE = new Set(["QUEUED", "CAPTURING", "SCANNING", "PUBLISHING"]);

/** Short commit id in code type; the full id is the tooltip and the accessible name. */
export function Commit({ sha }: { sha: string | null | undefined }) {
  if (!sha) return <span className="muted">—</span>;
  return (
    <code className="hash" title={sha} aria-label={`commit ${sha}`}>
      {sha.slice(0, 7)}
    </code>
  );
}

/** What a review looked at, in plain words: "main at abc1234" or "Pull request #7". */
export function ReviewTarget({ review }: { review: CodeReview }) {
  if (review.kind === "pull_request") {
    return (
      <span className="stack stack-xs">
        <span>
          Pull request #{review.pr_number}
          {review.pr_title ? `: ${review.pr_title}` : ""}
        </span>
        {review.fork ? <span className="small muted">From a fork</span> : null}
      </span>
    );
  }
  return (
    <span>
      {review.ref ?? "Default branch"}
      {review.head_sha ? (
        <>
          {" "}
          at <Commit sha={review.head_sha} />
        </>
      ) : null}
    </span>
  );
}

function count(result: Record<string, unknown> | null | undefined, key: string): number {
  const value = result?.[key];
  return typeof value === "number" ? value : 0;
}

/** "2 new · 1 fixed" for finished reviews; the reason for those that did not run. */
export function ReviewOutcome({ review }: { review: CodeReview }) {
  if (review.state === "SUCCEEDED" || review.state === "PARTIAL") {
    const fresh = count(review.result, "new");
    const fixed = count(review.result, "fixed");
    return (
      <span className="small" data-testid="review-outcome">
        <strong>{fresh === 0 ? "No new problems" : plural(fresh, "new problem")}</strong>
        {fixed > 0 ? ` · ${String(fixed)} fixed` : ""}
      </span>
    );
  }
  if (review.error_message) return <span className="small muted">{review.error_message}</span>;
  return null;
}

/** Where an upload came from when it is a GitHub commit (shown next to the upload identity). */
export function GitOrigin({ snapshot }: { snapshot: Snapshot }) {
  if (!snapshot.git_provider || !snapshot.git_commit) return null;
  return (
    <p className="row small secondary" data-testid="git-origin">
      <Icon name="branch" size={14} />
      <span>
        From GitHub: <strong>{snapshot.git_repository}</strong>
        {snapshot.git_ref ? ` · ${snapshot.git_ref}` : ""} · commit{" "}
        <Commit sha={snapshot.git_commit} />
      </span>
    </p>
  );
}

function listed(value: unknown): { count: number; paths: string[] } {
  if (typeof value !== "object" || value === null) return { count: 0, paths: [] };
  const item = value as { count?: unknown; paths?: unknown };
  return {
    count: typeof item.count === "number" ? item.count : 0,
    paths: Array.isArray(item.paths) ? item.paths.map(String) : [],
  };
}

/** Plain summary of how a commit capture was checked against the commit (technical details). */
export function CaptureCheck({ capture }: { capture: Record<string, unknown> | null | undefined }) {
  if (!capture) return null;
  const fetched = listed(capture.fetched);
  const missing = listed(capture.not_in_archive);
  const differs = listed(capture.differs_from_commit);
  const submodules = listed(capture.submodules);
  const lfs = listed(capture.git_lfs);
  const verified = typeof capture.verified === "number" ? capture.verified : 0;
  return (
    <ul className="plain-list stack stack-xs small secondary" data-testid="capture-check">
      <li>{plural(verified, "file")} matched the commit exactly.</li>
      {fetched.count > 0 ? (
        <li>
          {plural(fetched.count, "file")} missing from GitHub's download or changed in it were
          fetched one by one as committed.
        </li>
      ) : null}
      {missing.count + differs.count > 0 ? (
        <li>
          {plural(missing.count + differs.count, "file")} could not be fetched and were not
          reviewed.
        </li>
      ) : null}
      {submodules.count > 0 ? (
        <li>{plural(submodules.count, "submodule")} (separate repositories) were not included.</li>
      ) : null}
      {lfs.count > 0 ? (
        <li>{plural(lfs.count, "large file")} stored outside Git (LFS) were not included.</li>
      ) : null}
      {capture.tree_truncated === true ? (
        <li>The repository is too large for GitHub to list in full; not every file was checked.</li>
      ) : null}
    </ul>
  );
}
