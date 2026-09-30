import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 4 browser flows on the real stack and real engines: synthetic SAP Commerce and
// Salesforce uploads are reviewed, platform support (version, coverage) is shown and framework
// components can be found in the architecture map.

async function review(page: Page, zip: string, finalState: RegExp) {
  await page.getByLabel("ZIP archive to upload").setInputFiles(env(zip));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(finalState, { timeout: 150_000 });
}

test("SAP Commerce: platform support, findings and bean wiring", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `P04 SAP ${Date.now()}`);
  await review(page, "CRP_E2E_SAP_ZIP", /Partly complete/);

  const pack = page.getByTestId("framework-sap-commerce");
  await expect(pack).toContainText("SAP Commerce");
  await expect(pack.getByTestId("framework-version")).toHaveText("Version 2211.28");
  await expect(pack).toContainText("Experimental");
  await expect(
    pack.locator('[data-state="unavailable"]', { hasText: "Build and runtime" }),
  ).toHaveCount(1);
  await expect(page.getByTestId("check-frameworks")).toBeVisible();
  await expect(page.getByTestId("check-pmd-apex")).toHaveCount(0); // no Apex: not shown
  await page.screenshot({ path: "test-results/screens/p04-sap-review.png", fullPage: true });

  await page.getByLabel("Search findings by title or path").fill("FlexibleSearch");
  await expect(page.getByTestId("finding-row").first()).toBeVisible();
  await page.getByTestId("finding-row").first().click();
  await expect(page.getByTestId("finding-location")).toContainText("DefaultShopProductDao.java");
  await expect(page.getByTestId("recommendation")).toContainText("addQueryParameter");

  await page.goBack();
  await page.getByTestId("project-link").click();
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Architecture" })
    .click();
  await expect(page.getByTestId("frameworks")).toBeVisible();
  await page.getByLabel("Node kind").selectOption("component");
  await page.getByLabel("Search graph nodes").fill("loyaltyFacade");
  await page.getByTestId("graph-node-result").first().click();
  const hood = page.getByTestId("neighborhood");
  await expect(hood).toContainText(/Spring (alias|bean)/);
  await expect(page.getByTestId("graph-edge-row").first()).toBeVisible();
  await page.screenshot({ path: "test-results/screens/p04-sap-architecture.png", fullPage: true });
});

test("Salesforce: Apex checks, retired API version and platform support", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `P04 Salesforce ${Date.now()}`);
  await review(page, "CRP_E2E_SALESFORCE_ZIP", /Partly complete/);

  const pack = page.getByTestId("framework-salesforce");
  await expect(pack.getByTestId("framework-version")).toHaveText("Version 62.0");
  await expect(pack.locator('[data-state="partial"]').first()).toBeVisible(); // refused DTD file
  await expect(page.getByTestId("check-pmd-apex")).toContainText("Salesforce Apex");

  await page.getByLabel("Check").selectOption("pmd-apex");
  await expect(page.getByTestId("finding-row").first()).toBeVisible();
  await page.getByLabel("Search findings by title or path").fill("Retired Salesforce API");
  await page.getByLabel("Check").selectOption("");
  await expect(page.getByTestId("finding-row")).toHaveCount(1);
  await page.getByTestId("finding-row").first().click();
  await expect(page.getByTestId("finding-location")).toContainText(
    "LegacyIntegration.cls-meta.xml",
  );
  await page.screenshot({ path: "test-results/screens/p04-sf-finding.png", fullPage: true });

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.goBack();
  await expect(page.getByTestId("framework-salesforce")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/p04-sf-review-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = (): Promise<unknown> =>
    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
  expect(await overflow()).toBe(0);
  await page.screenshot({ path: "test-results/screens/p04-sf-review-mobile.png", fullPage: true });
});
