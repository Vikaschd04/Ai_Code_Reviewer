import { expect, test, type Page } from "@playwright/test";

import { signIn } from "./helpers";

// Reviewer-first UI on the real stack: the demo sign-in, the one-click sample review (real
// engines), every main screen, and screenshots in light, dark and mobile layouts for design
// review (test-results/screens/tour-*.png).

async function shot(page: Page, name: string) {
  await page.screenshot({ path: `test-results/screens/tour-${name}.png`, fullPage: true });
}

async function setTheme(page: Page, theme: "light" | "dark") {
  const toggle = page.getByRole("button", { name: `Switch to ${theme} theme` });
  if (await toggle.isVisible()) {
    await toggle.click();
    await page.waitForTimeout(400); // let colour transitions finish before a screenshot
  }
}

test("demo sign-in, sample dry run and every review screen", async ({ page }) => {
  test.setTimeout(300_000);
  await page.goto("/");
  await expect(
    page.getByRole("heading", { level: 1, name: /Ship code you can trust/ }),
  ).toBeVisible();
  await shot(page, "login");

  await page.getByRole("button", { name: "Try the demo" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: /Welcome to the refactorX demo/ }),
  ).toBeVisible();
  await expect(page.getByTestId("demo-banner")).toContainText("shared demo workspace");
  // The demo account is not an administrator and sees no technical pages.
  await expect(page.getByRole("link", { name: "System status" })).toHaveCount(0);
  await setTheme(page, "light");
  await shot(page, "overview-empty");

  // One-click dry run: create the sample, freeze it, review it with every real engine.
  await page
    .getByTestId("sample-card")
    .getByRole("button", { name: "Run the sample review" })
    .click();
  await expect(page).toHaveURL(/#\/scans\//, { timeout: 120_000 });
  await expect(page.getByTestId("stage-publish")).toContainText(/Complete/, { timeout: 180_000 });
  for (const check of ["pmd", "eslint", "opengrep", "trivy"]) {
    await expect(page.getByTestId(`stage-${check}`)).toContainText("Complete");
  }
  // Duplicates reported by two checks are merged into one row.
  await expect(page.getByTestId("also-reported").first()).toContainText("Also found by");
  await expect(page.getByTestId("finding-row").first()).toContainText("Critical");
  // Technical provenance stays collapsed until asked for.
  await expect(page.getByTestId("engine-pmd")).toBeHidden();
  await shot(page, "review");
  await setTheme(page, "dark");
  await shot(page, "review-dark");
  await setTheme(page, "light");

  await page.getByLabel("Search findings by title or path").fill("config.js");
  await page.getByTestId("finding-row").first().click();
  await expect(page.getByRole("heading", { name: "How to fix" })).toBeVisible();
  await expect(page.getByTestId("finding-technical")).not.toHaveAttribute("open", "");
  await shot(page, "finding");

  await page.getByRole("link", { name: "Back to review results" }).click();
  await page.getByRole("link", { name: "Files checked" }).click();
  await expect(page.getByTestId("coverage-row").first()).toBeVisible();
  await page.getByRole("link", { name: "Changes" }).click();
  await expect(page.getByText("Nothing to compare with yet")).toBeVisible();

  await page.getByTestId("project-link").click();
  await expect(page.getByRole("heading", { level: 1, name: /Sample: Online store/ })).toBeVisible();
  await shot(page, "project");
  await page.getByRole("link", { name: "Issues", exact: true }).click();
  await expect(page.getByTestId("issue-row").first()).toBeVisible();
  await shot(page, "issues");
  await page.getByRole("link", { name: "Architecture" }).click();
  await expect(page.getByTestId("architecture")).toBeVisible();
  await shot(page, "architecture");
  await page.getByRole("link", { name: /^Uploads/ }).click();
  await page.getByRole("link", { name: "online-store-sample.zip" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "online-store-sample.zip" }),
  ).toBeVisible();
  await expect(page.getByRole("list", { name: "Detected technologies" })).toBeVisible();
  await shot(page, "upload");

  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Projects" })
    .click();
  await expect(page.getByRole("link", { name: "Sample: Online store" })).toBeVisible();
  await shot(page, "projects");
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Overview" })
    .click();
  await expect(page.getByRole("heading", { name: "Recent projects" })).toBeVisible();
  await shot(page, "overview");
  await setTheme(page, "dark");
  await shot(page, "overview-dark");

  // Mobile layout: compact navigation, no horizontal page scroll.
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  const overflow: unknown = await page.evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth",
  );
  expect(Number(overflow)).toBeLessThanOrEqual(1);
  await shot(page, "overview-mobile");
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Projects" })
    .click();
  await page.getByRole("link", { name: "Sample: Online store" }).first().click();
  await page.getByRole("link", { name: /^Reviews/ }).click();
  await page.getByRole("link", { name: "Review 1" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Complete/);
  await shot(page, "review-mobile");
  await page.getByRole("button", { name: "Sign out" }).click();
  await expect(page.getByRole("button", { name: "Try the demo" })).toBeVisible();
  await shot(page, "login-mobile");
});

test("the owner sees system status; screens use plain language", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "System status" }).click();
  await expect(page.getByTestId("check-database")).toBeVisible({ timeout: 30_000 });
  await shot(page, "system-status");
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Projects" })
    .click();
  await expect(page.getByRole("heading", { name: "Try the sample project" })).toBeVisible();
});
