import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 2 browser flows on the real stack and real engines (PMD, ESLint, Opengrep, Trivy with an
// offline DB): dependency and secret findings, cross-engine correlation, triage, exports,
// comparison, issues and the architecture graph. Archives are synthetic fixtures.

async function uploadAndScan(page: Page, zip: string, finalState: RegExp) {
  await page.getByLabel("ZIP archive to upload").setInputFiles(env(zip));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(finalState, { timeout: 150_000 });
}

test("security findings, correlation, triage, exports and comparison", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `P02 security ${Date.now()}`);
  await uploadAndScan(page, "CRP_E2E_SECURITY_ZIP", /Complete/);
  await page.getByTestId("checks-panel").getByText("What was checked").click();
  await page.getByTestId("checks-technical").getByText("Technical details").click();
  await expect(page.getByTestId("engine-opengrep")).toContainText("1.30.0");
  await expect(page.getByTestId("engine-trivy")).toContainText("0.69.3");
  await expect(page.getByTestId("engine-trivy")).toContainText("Vulnerability DB");
  await page.screenshot({ path: "test-results/screens/p02-scan.png", fullPage: true });

  // Cross-engine correlation: ESLint and Opengrep report the same eval() call.
  await page.getByLabel("Search findings by title or path").fill("server.js");
  await expect(page.getByTestId("also-reported").first()).toContainText("Also found by");

  // Dependency finding without an invented line.
  await page.getByLabel("Search findings by title or path").fill("CVE-2021-44228");
  await page.getByTestId("finding-row").first().click();
  await expect(page.getByTestId("finding-location")).toHaveText("pom.xml · dependency");
  await expect(page.getByTestId("dependency-card")).toContainText("2.14.1");
  const panel = page.getByTestId("issue-panel");
  await expect(panel).toContainText("Still present");
  await panel.getByLabel("Status").selectOption("ACCEPTED_RISK");
  await panel.getByLabel("Reason (required)").fill("Upgrade scheduled in the next release");
  await panel.getByLabel("Owner").fill("platform-team");
  await panel.getByRole("button", { name: "Save decision" }).click();
  await expect(panel.getByRole("status")).toContainText("Saved");
  await expect(panel).toContainText("Accepted risk");
  await page.screenshot({ path: "test-results/screens/p02-finding.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/p02-finding-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();

  // Exports: SARIF 2.1.0 download.
  await page.getByRole("link", { name: "Back to review results" }).click();
  const downloadPromise = page.waitForEvent("download");
  await page.getByTestId("export-sarif").click();
  const download = await downloadPromise;
  const sarif = JSON.parse(readFileSync(await download.path(), "utf8")) as { version: string };
  expect(sarif.version).toBe("2.1.0");

  // Re-run with cache reuse, then compare with the first scan.
  const firstReview = page.url();
  await page.getByRole("button", { name: "Review again", exact: true }).click();
  // Wait for the new review's page; the first review's page also says "Complete".
  await expect(page).not.toHaveURL(firstReview);
  await expect(page.getByTestId("stage-publish")).toContainText(/Complete/, { timeout: 150_000 });
  await expect(page.getByTestId("cache-pmd")).toContainText("reused");
  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByTestId("compare-new")).toContainText("0");
  await expect(page.getByTestId("compare-unchanged")).not.toContainText(/^\s*Still present\s*0/);
  await page.screenshot({ path: "test-results/screens/p02-compare.png", fullPage: true });

  // Project issues: the accepted risk is visible with its owner.
  await page.getByTestId("project-link").click();
  await page.getByRole("link", { name: "Issues", exact: true }).click();
  await expect(page.getByTestId("issue-row").first()).toBeVisible();
  await page.getByRole("button", { name: /Accepted risk/ }).click();
  await expect(page.getByTestId("issue-row")).toHaveCount(1);
  await expect(page.getByTestId("issue-row")).toContainText("platform-team");
  await page.screenshot({ path: "test-results/screens/p02-issues.png", fullPage: true });
});

test("architecture graph with bounded neighborhood and impact", async ({ page }) => {
  test.setTimeout(240_000);
  await signIn(page);
  await createProject(page, `P02 graph ${Date.now()}`);
  await uploadAndScan(page, "CRP_E2E_GRAPH_ZIP", /Partly complete/);
  await expect(page.getByTestId("check-graph")).toContainText("Partly complete");
  await page.getByTestId("upload-link").click();
  await page.getByRole("link", { name: "Architecture" }).click();
  await expect(page.getByTestId("architecture")).toBeVisible();
  // Structure health (P10): parts, cycles and Martin metrics measured from the code.
  const health = page.getByTestId("architecture-health");
  await expect(health).toContainText("Structure health");
  await expect(health.locator('[data-tile="Parts"] .tile-value')).not.toHaveText("0");
  await health.getByText("All parts and their measurements").click();
  await expect(
    health.getByTestId("architecture-metrics").locator('[data-component="com.acme.core"]'),
  ).toBeVisible();
  await health.getByText("Dependency matrix", { exact: true }).click();
  await expect(health.getByTestId("architecture-dsm")).toBeVisible();
  await health.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/screens/p10-structure-health.png", fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
  await page.screenshot({
    path: "test-results/screens/p10-structure-health-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByText("Show module connections as a table").click();
  await expect(
    page.getByTestId("module-dependency").filter({ hasText: "app (Maven)" }).first(),
  ).toBeVisible();
  await page.getByLabel("Search graph nodes").fill("CustomerRepository");
  await expect(page.getByTestId("graph-node-result").first()).toContainText("CustomerRepository");
  await page.getByTestId("graph-node-result").first().click();
  const hood = page.getByTestId("neighborhood");
  await expect(
    hood.getByTestId("graph-edge-row").filter({ hasText: "org.slf4j.Logger" }),
  ).toContainText("Likely");
  await expect(
    hood.getByTestId("graph-edge-row").filter({ hasText: "com.unknown.Missing" }),
  ).toContainText("Not found");
  await page.screenshot({ path: "test-results/screens/p02-architecture.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/p02-architecture-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.getByLabel("Search graph nodes").fill("Entity");
  await page.getByLabel("Node kind").selectOption("type");
  // Wait for the new results before clicking (the previous search's list is still shown).
  await expect(page.getByTestId("graph-node-result")).toHaveCount(1);
  await expect(page.getByTestId("graph-node-result").first()).toContainText("Entity.java");
  await page.getByTestId("graph-node-result").first().click();
  await expect(hood.getByRole("heading", { name: "Entity" })).toBeVisible();
  await hood.getByRole("button", { name: "Impact" }).click();
  await expect(page.getByTestId("impact")).toContainText("Customer.java");
  await expect(page.getByTestId("impact")).toContainText("unresolved edge");
});
