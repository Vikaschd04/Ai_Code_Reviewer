import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Duplicate code (ADD-A3): the review runs PMD's copy/paste detector; copied blocks become issues
// of the NFR checkpoint "Logic is not copy-pasted" with steps to resolve them. The fixture copies
// a pricing routine between two Java services and a totals function between two TypeScript files.

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
}

test("duplicate code: found by the review and resolved through its checkpoint", async ({
  page,
}) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `Duplicates ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_DUPLICATES_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });
  await expect(page.getByTestId("check-cpd")).toContainText("Duplicate code");

  await page.goto(`/#/projects/${project}?tab=insights`);
  const checkpoint = page
    .getByTestId("needs-work")
    .getByTestId("checkpoint-architecture.duplication");
  await expect(checkpoint).toHaveAttribute("data-status", "attention");
  await expect(checkpoint).toContainText("Logic is not copy-pasted");
  await expect(checkpoint).toContainText("Move the shared logic into one function or class");
  await expect(checkpoint.getByTestId("checkpoint-fix-automatically")).toHaveCount(0);
  await checkpoint.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/screens/duplicates.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/duplicates-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/duplicates-mobile.png", fullPage: true });

  // Health over time (ADD-A6): a second review of the same upload makes a trend on the Overview.
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto(`/#/projects/${project}`);
  await page.getByRole("button", { name: "Review latest upload" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });
  await page.goto(`/#/projects/${project}`);
  const trend = page.getByTestId("health-trend");
  await expect(trend).toContainText("What each of the last 2 reviews found");
  await expect(trend.getByTestId("trend-tiles")).toContainText("Same as the previous review");
  await trend.locator(".trend-column").first().focus();
  await expect(trend.getByRole("status")).toContainText("critical and high");
  await trend.getByTestId("trend-table").locator("summary").click();
  await expect(trend.getByTestId("trend-table").locator("tbody tr")).toHaveCount(2);
  await trend.scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/screens/health-trend.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/health-trend-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/health-trend-mobile.png", fullPage: true });
});
