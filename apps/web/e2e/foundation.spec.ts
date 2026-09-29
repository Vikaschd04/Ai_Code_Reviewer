import { expect, test } from "@playwright/test";

import { createProject, signIn } from "./helpers";

// Runs against the isolated stack started by `make test-e2e`: real PostgreSQL, Temporal,
// API, worker and the production web build. Nothing here is mocked.

test("rejects a wrong token with an understandable message", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Access token").fill("definitely-not-the-token");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("alert")).toContainText("The token is not valid");
  await expect(page.getByRole("button", { name: "Sign in" })).toBeVisible();
});

test("shows real service readiness on the operations page", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "System status" }).click();
  await expect(
    page.getByRole("heading", { level: 2, name: "All services are running" }),
  ).toBeVisible({
    timeout: 30_000,
  });
  await expect(async () => {
    await page.getByRole("button", { name: "Re-check" }).click();
    await expect(page.getByTestId("overall-readiness")).toContainText("Ready", { timeout: 2_000 });
  }).toPass({ timeout: 30_000 });
  for (const name of ["database", "workflow_service", "workflow_worker", "artifact_store"]) {
    await expect(page.getByTestId(`check-${name}`)).toContainText("OK");
  }
  await expect(page.getByTestId("check-database")).toContainText("schema at head");
  // Future capabilities are announced as "coming soon", never as working links.
  await expect(page.getByRole("list", { name: "Coming soon" })).toContainText("AI investigation");
  await expect(page.getByRole("link", { name: "AI investigation" })).toHaveCount(0);
});

test("runs the durable diagnostic workflow on the real worker", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "System status" }).click();
  await page.getByText("Advanced: test the review pipeline").click();
  await page.getByRole("button", { name: "Run diagnostic workflow" }).click();
  await expect(page.getByTestId("diagnostic-status")).toHaveText("COMPLETED", { timeout: 45_000 });
  const result = page.getByTestId("diagnostic-result");
  await expect(result).toContainText("verified");
  await expect(result).toContainText("cleaned up");
  await expect(result).toContainText("crp-worker@");
});

test("creates a project that persists across reloads", async ({ page }) => {
  await signIn(page);
  const name = `E2E project ${Date.now()}`;
  await createProject(page, name);
  await page.reload();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Projects" })
    .click();
  await expect(page.getByRole("link", { name })).toBeVisible();
});

test("theme toggle switches between dark and light", async ({ page }) => {
  await signIn(page);
  const html = page.locator("html");
  const initial = await html.getAttribute("data-theme");
  const toggle = page.getByRole("button", { name: /Switch to (light|dark) theme/ });
  await toggle.click();
  await expect(html).not.toHaveAttribute("data-theme", initial ?? "");
});

test("signing out returns to the sign-in form", async ({ page }) => {
  await signIn(page);
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByLabel("Access token")).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("Access token")).toBeVisible();
});
