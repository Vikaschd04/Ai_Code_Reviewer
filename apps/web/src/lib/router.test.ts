import { describe, expect, it } from "vitest";

import { parseRoute, workspaceHref } from "./router";

const ID = "3f2c8f1e-0000-4000-8000-000000000001";

describe("parseRoute", () => {
  it("maps hashes to typed routes with default tabs", () => {
    expect(parseRoute("")).toEqual({ name: "dashboard" });
    expect(parseRoute("#/projects")).toEqual({ name: "projects" });
    expect(parseRoute(`#/projects/${ID}`)).toEqual({
      name: "project",
      id: ID,
      tab: "overview",
      check: null,
      view: null,
    });
    expect(parseRoute(`#/projects/${ID}?tab=source`)).toEqual({
      name: "project",
      id: ID,
      tab: "source",
      check: null,
      view: null,
    });
    expect(parseRoute(`#/projects/${ID}?tab=issues&check=architecture`)).toEqual({
      name: "project",
      id: ID,
      tab: "issues",
      check: "architecture",
      view: null,
    });
    expect(parseRoute(`#/projects/${ID}?tab=insights&view=nfr`)).toEqual({
      name: "project",
      id: ID,
      tab: "insights",
      check: null,
      view: "nfr",
    });
    expect(parseRoute(`#/scans/${ID}`)).toEqual({ name: "scan", id: ID, tab: "findings" });
    expect(parseRoute(`#/findings/${ID}`)).toEqual({ name: "finding", id: ID });
    expect(parseRoute(`#/ai-runs/${ID}`)).toEqual({ name: "ai-run", id: ID });
    expect(parseRoute(`#/fixes/${ID}`)).toEqual({ name: "fix", id: ID });
    expect(parseRoute(`#/reviews/${ID}`)).toEqual({ name: "review", id: ID });
  });

  it("passes GitHub sign-in results to the GitHub page", () => {
    expect(parseRoute("#/github?code=abc&state=xyz")).toEqual({
      name: "github",
      params: { code: "abc", state: "xyz" },
    });
    expect(parseRoute("#/github")).toEqual({ name: "github", params: {} });
  });

  it("rejects unknown paths and malformed identifiers", () => {
    expect(parseRoute("#/scans/not-a-uuid")).toEqual({ name: "not-found" });
    expect(parseRoute("#/admin")).toEqual({ name: "not-found" });
    expect(parseRoute(`#/findings/${ID}/../../etc`)).toEqual({ name: "not-found" });
  });
});

describe("snapshot tabs", () => {
  it("defaults to scope and accepts the architecture tab", () => {
    expect(parseRoute(`#/snapshots/${ID}`)).toEqual({ name: "snapshot", id: ID, tab: "scope" });
    expect(parseRoute(`#/snapshots/${ID}?tab=architecture`)).toEqual({
      name: "snapshot",
      id: ID,
      tab: "architecture",
    });
  });
});

describe("fix workspaces", () => {
  it("opens the issues tab by default and carries a file and line", () => {
    expect(parseRoute(`#/workspaces/${ID}`)).toEqual({
      name: "workspace",
      id: ID,
      tab: "issues",
      path: null,
      line: null,
      issues: [],
    });
    expect(parseRoute(`#/workspaces/${ID}?tab=edit&path=src%2Fa%20b.ts&line=12`)).toEqual({
      name: "workspace",
      id: ID,
      tab: "edit",
      path: "src/a b.ts",
      line: 12,
      issues: [],
    });
  });

  it("ignores lines that are not positive whole numbers", () => {
    for (const line of ["0", "-3", "1.5", "x"]) {
      const route = parseRoute(`#/workspaces/${ID}?tab=edit&path=a.js&line=${line}`);
      expect(route).toMatchObject({ name: "workspace", line: null });
    }
  });

  it("builds links that parse back to the same place", () => {
    expect(workspaceHref(ID)).toBe(`#/workspaces/${ID}`);
    const href = workspaceHref(ID, "edit", "web/src/cart & co.ts", 7);
    expect(parseRoute(href)).toEqual({
      name: "workspace",
      id: ID,
      tab: "edit",
      path: "web/src/cart & co.ts",
      line: 7,
      issues: [],
    });
  });

  it("carries the issues of a recommendation and drops anything that is not an id", () => {
    const other = "3f2c8f1e-0000-4000-8000-000000000002";
    const href = workspaceHref(ID, "issues", null, null, [ID, other]);
    expect(parseRoute(href)).toMatchObject({
      name: "workspace",
      tab: "issues",
      issues: [ID, other],
    });
    expect(parseRoute(`#/workspaces/${ID}?issues=${ID},nope,..%2F`)).toMatchObject({
      issues: [ID],
    });
  });
});
