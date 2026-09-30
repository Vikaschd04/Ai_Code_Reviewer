import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DiffView, parseDiff } from "./Fixes";

const PATCH = [
  "diff --git a/src/A.java b/src/A.java",
  "--- a/src/A.java",
  "+++ b/src/A.java",
  "@@ -8,3 +8,3 @@",
  "     public boolean isPaid(String status) {",
  '-        if (status == "PAID") {',
  '+        if ("PAID".equals(status)) {',
  "             return true;",
  "\\ No newline at end of file",
  "",
].join("\n");

describe("diff view", () => {
  it("numbers old and new lines and drops file headers", () => {
    const lines = parseDiff(PATCH);
    expect(lines.map((line) => line.kind)).toEqual([
      "hunk",
      "context",
      "del",
      "add",
      "context",
      "note",
    ]);
    expect(lines[2]).toMatchObject({ oldLine: 9, newLine: null });
    expect(lines[3]).toMatchObject({ oldLine: null, newLine: 9 });
    expect(lines[4]).toMatchObject({ oldLine: 10, newLine: 10 });
  });

  it("shows code as inert text with non-colour cues for changes", () => {
    const { container } = render(
      <DiffView patch={"@@ -1 +1 @@\n-<script>alert(1)</script>\n+safe()\n"} path="x.js" />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(screen.getByText("<script>alert(1)</script>")).toBeInTheDocument();
    expect(screen.getByText("Removed:")).toBeInTheDocument();
    expect(screen.getByText("Added:")).toBeInTheDocument();
  });
});
