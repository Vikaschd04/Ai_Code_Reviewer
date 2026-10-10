import { readFileSync } from "node:fs";

import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 8 browser flow on the real stack and real engines: a fix workspace on a reviewed
// upload fixes issues in bulk, a file is edited in the code editor (a hiding marker is flagged),
// the changes are compared and re-checked on a copy of the upload (hidden issues are never
// "fixed"), and the patch and the changed files are downloaded. Then two uploads are compared.

async function uploadZip(page: Page, zip: string) {
  await page.getByLabel("ZIP archive to upload").setInputFiles(zip);
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
}

/** The code viewer measures lines once visible; bring it into view before a screenshot. */
async function showCode(page: Page) {
  await page.getByTestId("compare-view").scrollIntoViewIfNeeded();
  await page.evaluate(
    "new Promise((done) => requestAnimationFrame(() => requestAnimationFrame(done)))",
  );
}

/** Replace the editor's selection the way a paste does (multi-line text arrives in one piece). */
async function paste(page: Page, text: string) {
  await page.evaluate(`(() => {
    const target = document.querySelector('[data-testid="code-editor"] .cm-content');
    const data = new DataTransfer();
    data.setData("text/plain", ${JSON.stringify(text)});
    target.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  })()`);
}

function noOverflow(page: Page): Promise<unknown> {
  return page.evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth",
  );
}

test("fix in bulk and by hand, re-check, compare and download", async ({ page }) => {
  test.setTimeout(420_000);
  await signIn(page);
  await createProject(page, `P08 workspace ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await uploadZip(page, env("CRP_E2E_SEEDED_ZIP"));
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete/, {
    timeout: 150_000,
  });

  // Open a workspace from the project.
  await page.goto(`/#/projects/${project}?tab=workspaces`);
  await page.getByTestId("workspace-new").click();
  await expect(page).toHaveURL(/#\/workspaces\//);
  const workspace = page
    .url()
    .replace(/^.*#\/workspaces\//, "")
    .replace(/\?.*$/, "");
  await expect(page.getByTestId("workspace-issues")).toBeVisible();

  // Fix every issue with an automatic fix in one go.
  await page.getByRole("button", { name: "Select all with an automatic fix" }).click();
  await page.getByTestId("workspace-fix-selected").click();
  await expect(page.getByTestId("workspace-fix-result")).toContainText(
    /Fixed \d+ issues? automatically/,
  );

  // AI suggestions (labelled test provider): off until a project admin switches AI on; each
  // suggestion is checked like an automatic fix and only checked ones can be applied.
  await page.goto(`/#/projects/${project}?tab=settings`);
  await page.getByLabel(/I am allowed to share this project's code/).check();
  await page.getByRole("button", { name: "Switch on AI review" }).click();
  await expect(page.getByTestId("ai-policy")).toHaveAttribute("data-enabled", "true");
  await page.goto(`/#/workspaces/${workspace}?tab=edit&path=web/src/app.js&line=10`);
  await page
    .getByTestId("workspace-file-issues")
    .locator("li", { hasText: "debugger" })
    .getByTestId("workspace-ask-ai")
    .click();
  const candidates = page.getByTestId("workspace-ai").getByTestId("ai-candidate");
  await expect(candidates).toHaveCount(2, { timeout: 120_000 });
  await expect(candidates.nth(0)).toHaveAttribute("data-applicable", "true");
  await expect(candidates.nth(1)).toHaveAttribute("data-applicable", "false");
  await expect(candidates.nth(1)).toContainText("suppression");
  await page.getByTestId("workspace-ai").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/screens/p08-ai.png", fullPage: true });
  await candidates.nth(0).getByTestId("ai-candidate-apply").click();
  await expect(page.getByTestId("workspace-ai")).toContainText("Applied");
  await expect(page.getByTestId("code-editor").locator(".cm-content")).not.toContainText(
    "debugger",
  );

  // Edit a file by hand: the line with the issue is selected; the hiding marker is flagged.
  await page.goto(`/#/workspaces/${workspace}?tab=edit&path=web/src/cart.ts&line=11`);
  const editor = page.getByTestId("code-editor").locator(".cm-content");
  await expect(editor).toContainText("item.price === NaN");
  await editor.focus();
  await paste(
    page,
    "    // eslint-disable-next-line no-dupe-keys\n    if (Number.isNaN(item.price)) {",
  );
  await expect(editor).toContainText("Number.isNaN(item.price)");
  await page.getByTestId("workspace-save").click();
  await expect(page.getByTestId("workspace-editor")).toContainText("hides problems");
  await expect(page.getByTestId("workspace-file-issues")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/p08-editor.png", fullPage: true });

  // Compare: the changed files and a side-by-side view of one of them.
  await page.getByRole("link", { name: /^Changes \(\d+\)$/ }).click();
  const files = page.getByTestId("workspace-files");
  await expect(files).toContainText("web/src/cart.ts");
  await expect(files).toContainText("InvoiceService.java");
  await files.getByRole("link", { name: /cart\.ts/ }).click();
  await page.getByRole("button", { name: "Side by side" }).click();
  await expect(page.getByTestId("compare-view")).toHaveAttribute("data-layout", "side");
  await expect(page.locator(".cm-mergeView")).toContainText("Number.isNaN(item.price)");
  await showCode(page);
  await page.screenshot({ path: "test-results/screens/p08-compare.png", fullPage: true });

  // Re-check the changes on a copy of the upload.
  await page.getByRole("link", { name: "Issues", exact: true }).click();
  await page.getByTestId("workspace-run-check").click();
  const outcomes = page.getByTestId("workspace-outcomes");
  await expect(outcomes).toBeVisible({ timeout: 180_000 });
  await expect(outcomes.locator('[data-outcome="fixed"] .tile-value')).not.toHaveText("0");
  await expect(outcomes.locator('[data-outcome="suppressed"] .tile-value')).not.toHaveText("0");
  await expect(page.getByTestId("workspace-check")).toContainText(
    "nothing is built, run or tested",
  );
  // Tier 0 type-check (P09): the hand edit fixed an always-false NaN comparison.
  await expect(page.getByTestId("workspace-types")).toHaveAttribute("data-state", "checked");
  await expect(page.getByTestId("workspace-types")).toContainText("type error fixed");
  await page.screenshot({ path: "test-results/screens/p08-workspace.png", fullPage: true });

  // Downloads: a patch for this exact upload and a ZIP with only the changed files.
  const patchDownload = page.waitForEvent("download");
  await page.getByTestId("workspace-export-patch").click();
  const patch = readFileSync(await (await patchDownload).path(), "utf8");
  expect(patch).toContain("# refactorX workspace");
  expect(patch).toContain('"PAID".equals(status)');
  expect(patch).toContain("Number.isNaN(item.price)");
  const zipDownload = page.waitForEvent("download");
  await page.getByTestId("workspace-export-changed").click();
  const zip = readFileSync(await (await zipDownload).path());
  expect(zip.subarray(0, 2).toString()).toBe("PK");

  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/p08-workspace-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await noOverflow(page)).toBe(0);
  await page.screenshot({ path: "test-results/screens/p08-workspace-mobile.png", fullPage: true });
  await page.goto(`/#/workspaces/${workspace}?tab=changes&path=web/src/cart.ts`);
  await expect(page.getByTestId("compare-view")).toHaveAttribute("data-layout", "inline");
  await showCode(page);
  expect(await noOverflow(page)).toBe(0);
  await page.screenshot({ path: "test-results/screens/p08-compare-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });

  // The upload itself is unchanged and the workspace copy is not listed as an upload.
  await page.goto(`/#/projects/${project}?tab=uploads`);
  await expect(page.getByRole("heading", { name: "Uploads (1)" })).toBeVisible();

  // Compare two uploads of the project.
  await page.goto(`/#/projects/${project}?tab=upload`);
  await uploadZip(page, env("CRP_E2E_SECURITY_ZIP"));
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("link", { name: "Compare" }).click();
  const compare = page.getByTestId("compare-uploads");
  await expect(compare.getByTestId("compare-counts")).toContainText("added");
  await compare.getByTestId("compare-changes").getByRole("button").first().click();
  await expect(page.getByTestId("compare-file")).toBeVisible();
  await expect(page.getByTestId("compare-view")).toBeVisible();
  await expect(page.getByTestId("compare-file")).toContainText("Removed in this upload.");
  await showCode(page);
  await page.screenshot({ path: "test-results/screens/p08-compare-uploads.png", fullPage: true });
});
