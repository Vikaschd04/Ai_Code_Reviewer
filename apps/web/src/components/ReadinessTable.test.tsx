import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DependencyCheck } from "../api/client";
import { ReadinessTable } from "./ReadinessTable";

// Synthetic display fixtures: these test rendering of each state, not service behaviour.
const checks: DependencyCheck[] = [
  {
    name: "database",
    status: "ok",
    latency_ms: 4.2,
    summary: "PostgreSQL reachable; schema at head",
    error_code: null,
    details: { schema_revision: "0001", expected_revision: "0001" },
  },
  {
    name: "workflow_worker",
    status: "unavailable",
    latency_ms: 12,
    summary: "No worker has recently polled task queue 'crp-main'; start the worker",
    error_code: "no_active_worker",
    details: { workflow_pollers: 0, newest_poll_age_seconds: null },
  },
  {
    name: "artifact_store",
    status: "failed",
    latency_ms: null,
    summary: "Artifact store write/read probe failed (PermissionError)",
    error_code: "artifact_store_failed",
    details: {},
  },
];

describe("ReadinessTable", () => {
  it("renders every state with text, not colour alone", () => {
    render(<ReadinessTable checks={checks} />);
    const database = screen.getByTestId("check-database");
    expect(within(database).getByText("OK")).toBeInTheDocument();
    expect(within(database).getByText("Database")).toBeInTheDocument();

    const worker = screen.getByTestId("check-workflow_worker");
    expect(within(worker).getByText("Unavailable")).toBeInTheDocument();
    expect(within(worker).getByText("no_active_worker")).toBeInTheDocument();
    expect(within(worker).getByText("—", { selector: "dd" })).toBeInTheDocument();

    const store = screen.getByTestId("check-artifact_store");
    expect(within(store).getByText("Failed")).toBeInTheDocument();
    expect(within(store).getAllByText("—")).toHaveLength(2);
  });

  it("uses a semantic table with a caption and row headers", () => {
    render(<ReadinessTable checks={checks} />);
    expect(screen.getByRole("table", { name: "Service status" })).toBeInTheDocument();
    expect(screen.getAllByRole("rowheader")).toHaveLength(3);
  });
});
