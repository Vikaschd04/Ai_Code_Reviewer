import { describe, expect, it } from "vitest";

import { editFlag } from "../lib/labels";
import { languageLoader } from "./CodeEditor";

describe("code editor", () => {
  it("colours the platform languages and leaves other files as plain text", () => {
    for (const path of [
      "src/A.java",
      "force-app/main/default/classes/Account.cls",
      "triggers/Order.trigger",
      "web/app.js",
      "web/app.tsx",
      "web/app.mts",
      "package.json",
      "pom.xml",
      "force-app/main/default/pages/Home.page",
    ]) {
      expect(languageLoader(path), path).not.toBeNull();
    }
    for (const path of ["README.md", "resources/impex/core.impex", "Makefile", "logo.png"]) {
      expect(languageLoader(path), path).toBeNull();
    }
  });

  it("explains policy flags in plain language", () => {
    expect(editFlag("suppression_added")).toContain("counted as hidden, not fixed");
    expect(editFlag("test_weakened")).toContain("tests");
    expect(editFlag("something_new")).toBe("something_new");
  });
});
