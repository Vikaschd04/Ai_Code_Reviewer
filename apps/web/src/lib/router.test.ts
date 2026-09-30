import { describe, expect, it } from "vitest";

import { parseRoute } from "./router";

const ID = "3f2c8f1e-0000-4000-8000-000000000001";

describe("parseRoute", () => {
  it("maps hashes to typed routes with default tabs", () => {
    expect(parseRoute("")).toEqual({ name: "dashboard" });
    expect(parseRoute("#/projects")).toEqual({ name: "projects" });
    expect(parseRoute(`#/projects/${ID}`)).toEqual({ name: "project", id: ID, tab: "overview" });
    expect(parseRoute(`#/projects/${ID}?tab=source`)).toEqual({
      name: "project",
      id: ID,
      tab: "source",
    });
    expect(parseRoute(`#/scans/${ID}`)).toEqual({ name: "scan", id: ID, tab: "findings" });
    expect(parseRoute(`#/findings/${ID}`)).toEqual({ name: "finding", id: ID });
    expect(parseRoute(`#/ai-runs/${ID}`)).toEqual({ name: "ai-run", id: ID });
    expect(parseRoute(`#/fixes/${ID}`)).toEqual({ name: "fix", id: ID });
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
