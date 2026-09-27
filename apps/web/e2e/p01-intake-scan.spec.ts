import { expect, test } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 1 browser flow on the real stack: ZIP intake → frozen snapshot → real PMD/ESLint scan →
// finding with exact source span → per-file coverage. Archives are synthetic fixtures generated
// by the E2E harness (`crp-dev test-e2e`).

test("uploads a ZIP, reviews scope, scans and navigates to exact source", async ({ page }) => {
  test.setTimeout(180_000);
  await signIn(page);
  await createProject(page, `P01 upload ${Date.now()}`);

  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_SEEDED_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();

  // Scope review: identity, exclusions and the untrusted instruction-file notice.
  await expect(page.getByTestId("manifest-sha")).toHaveText(/^[0-9a-f]{64}$/);
  await expect(page.getByText(/agent instruction files \(AGENTS\.md\)/)).toBeVisible();
  await page.getByRole("button", { name: "Excluded", exact: true }).click();
  await expect(page.getByRole("rowheader", { name: ".env" })).toBeVisible();
  await expect(page.getByRole("row", { name: /\.env/ })).toContainText("secret candidate");
  await expect(
    page.getByRole("rowheader", { name: "node_modules/leftpad/index.js" }),
  ).toBeVisible();
  await expect(
    page
      .getByRole("cell", { name: "Spring Boot" })
      .or(page.getByRole("rowheader", { name: "Spring Boot" })),
  ).toBeVisible();

  await page.getByRole("button", { name: "Start baseline scan" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partial/, { timeout: 120_000 });
  await expect(page.getByTestId("engine-pmd")).toContainText("7.27.0");
  await expect(page.getByTestId("engine-eslint")).toContainText("10.11.0");
  await expect(page.getByTestId("limitations")).toContainText("pmd: 1 of 3 eligible files failed");

  await page.getByLabel("Search findings by title or path").fill("Strings compared");
  await expect(page.getByTestId("finding-row")).toHaveCount(1);
  await page.getByTestId("finding-row").first().click();
  await expect(page.getByTestId("finding-location")).toHaveText(/InvoiceService\.java:10$/);
  const flagged = page.locator(".code-line.hit");
  await expect(flagged.first()).toHaveAttribute("data-line", "10");
  await expect(flagged.first()).toContainText('status == "PAID"');
  await expect(page.getByTestId("recommendation")).toContainText("equals()");

  await page.getByRole("link", { name: "Back to scan" }).click();
  await page.getByRole("link", { name: "Coverage" }).click();
  await page.getByLabel("Coverage outcome").selectOption("FAILED");
  // One row per file and engine; other engines may still parse these files (error recovery).
  const rows = page.getByTestId("coverage-row");
  await expect(rows.filter({ hasText: "Analyzed" })).toHaveCount(0);
  await expect(rows.filter({ hasText: "Broken.java" }).filter({ hasText: "pmd" })).toContainText(
    "Failed",
  );
  await expect(rows.filter({ hasText: "broken.ts" }).filter({ hasText: "eslint" })).toContainText(
    "Failed",
  );
});

test("rejects a traversal archive with an understandable message", async ({ page }) => {
  await signIn(page);
  await createProject(page, `P01 malicious ${Date.now()}`);
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_MALICIOUS_ZIP"));
  const rejection = page.getByTestId("intake-rejection");
  await expect(rejection).toBeVisible({ timeout: 60_000 });
  await expect(rejection).toContainText("Archive rejected");
  await expect(rejection).toContainText("path_traversal");
  await expect(
    rejection.locator("..").getByRole("listitem").filter({ hasText: "../../escape.sh" }),
  ).toBeVisible();
});
