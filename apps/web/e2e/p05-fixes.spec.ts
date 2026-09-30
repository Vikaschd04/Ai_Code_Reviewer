import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 5 browser flow on the real stack and real engines: a deterministic fix is prepared for
// a finding, shown as a diff, checked by the validation ladder (tests and build honestly "not
// run"), refused when an edit silences the check, and downloaded as a patch.

test("prepare, check, edit and download a fix", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `P05 fixes ${Date.now()}`);
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_SEEDED_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete/, {
    timeout: 150_000,
  });

  await page.getByLabel("Search findings by title or path").fill("Strings compared");
  await page.getByTestId("finding-row").first().click();
  const card = page.getByTestId("fix-card");
  await card.getByRole("button", { name: /Prepare fix: Compare the text with equals/ }).click();
  await expect(page).toHaveURL(/#\/fixes\//);
  const diff = page.getByTestId("diff");
  await expect(diff.locator('[data-kind="del"]')).toContainText('status == "PAID"');
  await expect(diff.locator('[data-kind="add"]')).toContainText('"PAID".equals(status)');
  await expect(page.getByText("What to watch:")).toBeVisible();
  await expect(page.getByTestId("fix-labels")).toContainText("Not validated yet");

  await page.getByRole("button", { name: "Run checks" }).click();
  const checks = page.getByTestId("fix-checks");
  await expect(checks.locator('[data-step="checks"]')).toHaveAttribute("data-state", "passed", {
    timeout: 90_000,
  });
  await expect(checks.locator('[data-step="tests"]')).toHaveAttribute("data-state", "not_run");
  await expect(checks.locator('[data-step="build"]')).toHaveAttribute("data-state", "not_run");
  await expect(page.getByTestId("fix-labels")).toContainText("Not compiled, built or tested");
  await page.screenshot({ path: "test-results/screens/p05-fix-passed.png", fullPage: true });

  // An edit that silences the check is refused; the fix stays as it was.
  await page.getByTestId("fix-editor").getByText("Edit the fix").click();
  const editor = page.getByTestId("fix-editor").locator("textarea").first();
  await editor.fill('        if (status == "PAID") { // NOPMD');
  await page.getByRole("button", { name: "Save changes" }).click();
  await expect(page.getByTestId("fix-editor").getByRole("alert")).toContainText("silences a check");

  const download = page.waitForEvent("download");
  await page.getByTestId("fix-download-patch").click();
  const patch = readFileSync(await (await download).path(), "utf8");
  expect(patch).toContain("# refactorX fix");
  expect(patch).toContain('+        if ("PAID".equals(status)) {');

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/p05-fix-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = (): Promise<unknown> =>
    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
  expect(await overflow()).toBe(0);
  await page.screenshot({ path: "test-results/screens/p05-fix-mobile.png", fullPage: true });

  await page.setViewportSize({ width: 1280, height: 800 });
  await page.getByRole("link", { name: "Back to the finding" }).click();
  await expect(page.getByTestId("fix-history")).toContainText("Checks passed");
  await page.getByTestId("fix-card").screenshot({ path: "test-results/screens/p05-fix-card.png" });
});
