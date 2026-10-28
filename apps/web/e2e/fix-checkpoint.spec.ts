import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// "Fix automatically" on an NFR checkpoint (ADD-A1): one button applies every automatic fix of
// the checkpoint's issues in a fix workspace and starts the check, which confirms them fixed.
// The configuration fixture runs one workload with a single instance and two without a replica
// count; the recipes set two instances. The upload itself is never changed.

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
}

test("fix a checkpoint automatically: fixes applied, check started, issues confirmed fixed", async ({
  page,
}) => {
  test.setTimeout(360_000);
  await signIn(page);
  await createProject(page, `Fix checkpoint ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_NFR_CONFIG_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  // Insights: the button says how many of the checkpoint's issues have an automatic fix.
  await page.goto(`/#/projects/${project}?tab=insights`);
  const work = page.getByTestId("needs-work");
  const instances = work.getByTestId("checkpoint-reliability.instances");
  const fix = instances.getByTestId("checkpoint-fix-automatically");
  await expect(fix).toHaveText(/Fix 3 issues automatically/);
  await expect(
    work
      .getByTestId("checkpoint-security.configuration")
      .getByTestId("checkpoint-fix-automatically"),
  ).toHaveText(/Fix \d+ of \d+ automatically/); // Trivy's findings are fixed by hand
  await instances.scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "test-results/screens/fix-checkpoint-insights.png",
    fullPage: true,
  });

  // One click: a workspace with the fixes applied and the check already running.
  await fix.click();
  await expect(page).toHaveURL(/#\/workspaces\/.*issues=/);
  const note = page.getByTestId("workspace-fix-result");
  await expect(note).toContainText("Fixed 3 issues automatically.");
  await expect(note).toContainText("A check of the changes was started");
  await expect(page.getByTestId("workspace-focus")).toContainText(
    "Showing the issues of one NFR checkpoint",
  );
  const outcomes = page.getByTestId("workspace-outcomes");
  await expect(outcomes).toBeVisible({ timeout: 180_000 });
  await expect(outcomes.locator('[data-outcome="fixed"] .tile-value')).toHaveText("3");
  await page.screenshot({
    path: "test-results/screens/fix-checkpoint-workspace.png",
    fullPage: true,
  });

  // The reviewer's summary (ADD-A5): what needs a person and the impact, as Markdown too.
  await page.getByRole("link", { name: /Changes/ }).click();
  const review = page.getByTestId("workspace-review-summary");
  await expect(review.getByTestId("review-needs-human")).toContainText(
    "refactorX checked the source only",
  );
  await expect(review.getByTestId("review-impact")).toContainText("Impact");
  await expect(review.getByTestId("review-copy")).toHaveText(/Copy as Markdown/);
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({
    path: "test-results/screens/fix-checkpoint-workspace-dark.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({
    path: "test-results/screens/fix-checkpoint-mobile.png",
    fullPage: true,
  });
});

test("upgrade vulnerable libraries automatically: one pom.xml upgrade fixes them all", async ({
  page,
}) => {
  test.setTimeout(360_000);
  await signIn(page);
  await createProject(page, `Upgrade libraries ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_SECURITY_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  // The vulnerable-libraries checkpoint: log4j-core (pom.xml) can be upgraded, lodash (npm
  // lockfile) is fixed by hand.
  await page.goto(`/#/projects/${project}?tab=insights`);
  const libraries = page.getByTestId("needs-work").getByTestId("checkpoint-security.dependencies");
  const fix = libraries.getByTestId("checkpoint-fix-automatically");
  await expect(fix).toHaveText(/Fix \d+ of \d+ automatically/);
  const count = Number((await fix.innerText()).match(/Fix (\d+) of/)?.[1]);
  expect(count).toBeGreaterThan(0);
  await fix.click();

  await expect(page).toHaveURL(/#\/workspaces\/.*issues=/);
  await expect(page.getByTestId("workspace-fix-result")).toContainText(
    `Fixed ${String(count)} issue`,
  );
  const outcomes = page.getByTestId("workspace-outcomes");
  await expect(outcomes).toBeVisible({ timeout: 180_000 });
  await expect(outcomes.locator('[data-outcome="fixed"] .tile-value')).toHaveText(String(count));
  await page.getByRole("link", { name: /Changes/ }).click();
  await expect(page.locator("main")).toContainText("Upgrade log4j-core 2.14.1 to");
  await page.screenshot({ path: "test-results/screens/fix-dependencies.png", fullPage: true });
});

test("fix first: the most important issues across checkpoints, safe ones fixed in one click", async ({
  page,
}) => {
  test.setTimeout(360_000);
  await signIn(page);
  await createProject(page, `Fix first ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_NFR_CONFIG_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  await page.goto(`/#/projects/${project}?tab=insights`);
  const card = page.getByTestId("fix-first");
  const items = card.getByTestId("fix-first-items").locator("> li");
  await expect(items.first()).toContainText("Safe automatic fix");
  await expect(items.first()).toContainText("a safe automatic fix exists");
  expect(await items.count()).toBeLessThanOrEqual(10);
  const safe = await card.locator('li[data-fixable="true"]').count();
  expect(safe).toBeGreaterThan(0);
  await page.screenshot({ path: "test-results/screens/fix-first.png", fullPage: true });

  await card.getByTestId("fix-first-fix").click();
  await expect(page).toHaveURL(/#\/workspaces\/.*issues=/);
  await expect(page.getByTestId("workspace-fix-result")).toContainText(
    `Fixed ${String(safe)} issue`,
  );
  const outcomes = page.getByTestId("workspace-outcomes");
  await expect(outcomes).toBeVisible({ timeout: 180_000 });
  await expect(outcomes.locator('[data-outcome="fixed"] .tile-value')).toHaveText(String(safe));
  await page.goto(`/#/projects/${project}?tab=insights`);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByTestId("fix-first")).toBeVisible();
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/fix-first-mobile.png", fullPage: true });
});
