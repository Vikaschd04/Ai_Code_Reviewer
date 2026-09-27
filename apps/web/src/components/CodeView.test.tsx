import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CodeView } from "./CodeView";

describe("CodeView", () => {
  it("renders untrusted source as inert text and marks the flagged span", () => {
    const { container } = render(
      <CodeView
        content={{
          file_id: "f",
          path: "web/evil.js",
          language: "javascript",
          total_lines: 3,
          start_line: 1,
          end_line: 3,
          lines: [
            "const a = 1;",
            '<img src=x onerror="alert(1)"><script>alert(2)</script>',
            "done();",
          ],
          redactions: 1,
          truncated: false,
        }}
        from={2}
        to={2}
      />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(
      screen.getByText('<img src=x onerror="alert(1)"><script>alert(2)</script>'),
    ).toBeInTheDocument();
    expect(container.querySelector('[data-line="2"]')).toHaveClass("hit");
    expect(container.querySelector('[data-line="1"]')).not.toHaveClass("hit");
    expect(screen.getByText(/1 likely secret value\(s\) masked/)).toBeInTheDocument();
  });
});
