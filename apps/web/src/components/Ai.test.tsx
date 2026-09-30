import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AiRun } from "../api/client";
import { AnchorList, EvidenceBadge, runError, runTitle } from "./Ai";

const run = {
  kind: "question",
  question: "Where is the total computed?",
  target_paths: null,
  error_code: null,
  error_message: null,
} as unknown as AiRun;

describe("AI display helpers", () => {
  it("labels evidence by what was checked, never by model confidence", () => {
    render(
      <>
        <EvidenceBadge evidence="verified_anchor" />
        <EvidenceBadge evidence="hypothesis" />
        <EvidenceBadge evidence="rejected" />
        <EvidenceBadge evidence="something-new" />
      </>,
    );
    expect(screen.getByText("Checked against your code")).toBeInTheDocument();
    expect(screen.getAllByText("Not verified")).toHaveLength(2); // unknown values stay unverified
    expect(screen.getByText("References did not match")).toBeInTheDocument();
  });

  it("renders model-quoted code as inert text with each citation's check result", () => {
    const { container } = render(
      <AnchorList
        anchors={[
          {
            path: "src/orders.py",
            start_line: 6,
            end_line: 7,
            quote: "<script>alert(1)</script>",
            status: "verified",
            sha256: "a".repeat(64),
          },
          {
            path: "src/orders.py",
            start_line: 3,
            end_line: 3,
            quote: "eval(user_input)",
            status: "quote_mismatch",
            sha256: null,
          },
        ]}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(screen.getByText("Matches the code")).toBeInTheDocument();
    expect(screen.getByText("Quoted code is not there")).toBeInTheDocument();
  });

  it("titles runs and explains failures in plain language", () => {
    expect(runTitle(run)).toBe("Where is the total computed?");
    expect(runTitle({ ...run, kind: "file_review", target_paths: ["a/b/c.ts"] })).toBe(
      "Review of c.ts",
    );
    expect(runTitle({ ...run, kind: "file_review", target_paths: ["a.ts", "b.ts"] })).toBe(
      "Review of 2 files",
    );
    expect(runTitle({ ...run, kind: "finding_review" })).toBe("Second opinion on a finding");
    expect(runError(run)).toBeNull();
    expect(runError({ ...run, error_code: "monthly_token_limit" })).toMatch(/limit is used up/);
    expect(
      runError({ ...run, error_code: "provider_rate_limited", error_message: "slow down" }),
    ).toBe("The AI service returned an error: slow down");
  });
});
