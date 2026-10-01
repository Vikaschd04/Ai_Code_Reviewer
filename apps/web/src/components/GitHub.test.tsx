import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CodeReview } from "../api/client";
import { CaptureCheck, ReviewOutcome, ReviewTarget } from "./GitHub";

function review(overrides: Partial<CodeReview>): CodeReview {
  return {
    id: "r1",
    project_id: "p1",
    repository: "acme/shop",
    kind: "branch",
    trigger: "push",
    state: "SUCCEEDED",
    ref: "main",
    head_sha: "0123456789abcdef0123456789abcdef01234567",
    pr_number: null,
    pr_title: null,
    pr_author: null,
    pr_url: null,
    fork: false,
    base_ref: null,
    base_sha: null,
    merge_base_sha: null,
    full: false,
    head_snapshot_id: null,
    base_snapshot_id: null,
    head_scan_id: null,
    base_scan_id: null,
    changes: null,
    result: { new: 2, fixed: 1 },
    publish_state: "off",
    publish_error: null,
    published_at: null,
    superseded_by: null,
    error_code: null,
    error_message: null,
    created_at: "2026-10-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

describe("GitHub review labels", () => {
  it("names branch reviews by short commit and pull requests by number", () => {
    const { rerender } = render(<ReviewTarget review={review({})} />);
    expect(screen.getByText("0123456")).toHaveAttribute(
      "title",
      "0123456789abcdef0123456789abcdef01234567",
    );
    rerender(
      <ReviewTarget
        review={review({ kind: "pull_request", pr_number: 7, pr_title: "Faster cart", fork: true })}
      />,
    );
    expect(screen.getByText("Pull request #7: Faster cart")).toBeInTheDocument();
    expect(screen.getByText("From a fork")).toBeInTheDocument();
  });

  it("summarises results, or says why a review did not run", () => {
    const { rerender } = render(<ReviewOutcome review={review({})} />);
    expect(screen.getByTestId("review-outcome")).toHaveTextContent("2 new problems · 1 fixed");
    rerender(<ReviewOutcome review={review({ result: { new: 0, fixed: 0 } })} />);
    expect(screen.getByTestId("review-outcome")).toHaveTextContent("No new problems");
    rerender(
      <ReviewOutcome
        review={review({
          state: "SKIPPED",
          result: null,
          error_message: "This commit is already reviewed or being reviewed.",
        })}
      />,
    );
    expect(screen.getByText(/already reviewed/)).toBeInTheDocument();
  });

  it("explains how a capture was checked against the commit", () => {
    render(
      <CaptureCheck
        capture={{
          verified: 40,
          fetched: { count: 1, paths: ["hidden/A.java"] },
          not_in_archive: { count: 0, paths: [] },
          differs_from_commit: { count: 0, paths: [] },
          submodules: { count: 2, paths: [] },
          git_lfs: { count: 1, paths: [] },
          tree_truncated: false,
        }}
      />,
    );
    const text = screen.getByTestId("capture-check").textContent;
    expect(text).toContain("40 files matched the commit exactly.");
    expect(text).toContain("1 file missing from GitHub's download");
    expect(text).toContain("2 submodules");
    expect(text).toContain("1 large file stored outside Git");
  });
});
