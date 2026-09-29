import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { EngineRun } from "../api/client";
import { CoverageMeter } from "./Charts";
import { SeverityChip, SeverityStackBar, severityCounts } from "./Severity";

// Synthetic display fixtures: these test rendering, not service behaviour.
const run: EngineRun = {
  engine: "pmd",
  engine_version: "7.27.0",
  ruleset_id: "crp-pmd-java-v1",
  ruleset_sha256: null,
  state: "PARTIAL",
  files_eligible: 3,
  files_attempted: 3,
  files_succeeded: 2,
  files_failed: 1,
  findings_count: 12,
  exit_code: 0,
  duration_ms: 2600,
  error_code: null,
  error_message: null,
  diagnostics: null,
  cache_hits: 0,
  cache_misses: 0,
  raw_artifact_sha256: null,
  started_at: null,
  finished_at: null,
};

describe("severity visuals", () => {
  it("never relies on colour alone: legend carries label and count", () => {
    const counts = severityCounts({ critical: 1, high: 7, medium: 0, low: 9, info: 2, bogus: 5 });
    render(<SeverityStackBar counts={counts} label="Findings" />);
    expect(
      screen.getByRole("img", { name: /1 critical, 7 high, 9 low, 2 info/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("High").parentElement).toHaveTextContent("High 7");
    expect(screen.getByText("Medium").parentElement).toHaveTextContent("Medium 0");
  });

  it("renders an empty track when there are no findings", () => {
    const { container } = render(<SeverityStackBar counts={severityCounts({})} label="Findings" />);
    expect(container.querySelector(".stackbar-empty")).not.toBeNull();
  });

  it("shows unknown severities verbatim instead of guessing", () => {
    render(<SeverityChip severity="blocker" />);
    expect(screen.getByText("blocker")).toBeInTheDocument();
  });
});

describe("CoverageMeter", () => {
  it("states analyzed, failed and not-attempted counts against eligible files", () => {
    render(<CoverageMeter run={{ ...run, files_succeeded: 1, files_failed: 1 }} />);
    expect(
      screen.getByRole("img", {
        name: "1 of 3 eligible files analyzed, 1 failed, 1 not attempted",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/1 of 3 files checked · 1 could not be read · 1 not attempted/),
    ).toBeInTheDocument();
  });
});
