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
