import { describe, expect, it } from "vitest";

import type { Finding } from "../api/client";
import { findingLocation } from "./FindingPage";

const base = {
  path: "src/A.java",
  anchor_kind: "source_span",
  start_line: 10,
  end_line: 10,
} as unknown as Finding;

describe("findingLocation", () => {
  it("shows exact spans and never invents a line for dependency findings", () => {
    expect(findingLocation(base)).toBe("src/A.java:10");
    expect(findingLocation({ ...base, end_line: 12 })).toBe("src/A.java:10–12");
    expect(
      findingLocation({
        ...base,
        path: "pom.xml",
        anchor_kind: "dependency",
        start_line: null,
        end_line: null,
      }),
    ).toBe("pom.xml · dependency");
  });
});
