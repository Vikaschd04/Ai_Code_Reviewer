import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// P10 slice 2: a team writes its intended architecture, checks it on the latest upload, saves
// it, and the next review reports breaches as issues. The archive is a synthetic fixture.

const RULES = `layers:
  - name: core
    match: [com.acme.core.**]
  - name: app
    match: [com.acme.app.**]
forbid:
  - key: no-util
    from: app
    to: com.acme.core.util.**
    reason: Use the core API.
`;

/** Replace the editor's text the way a paste does (multi-line text arrives in one piece). */
async function replaceText(page: Page, text: string) {
  const editor = page.getByTestId("code-editor").locator(".cm-content");
  await editor.focus();
  await page.keyboard.press("ControlOrMeta+a");
  await page.evaluate(`(() => {
    const target = document.querySelector('[data-testid="code-editor"] .cm-content');
    const data = new DataTransfer();
    data.setData("text/plain", ${JSON.stringify(text)});
    target.dispatchEvent(new ClipboardEvent("paste", { clipboardData: data, bubbles: true, cancelable: true }));
  })()`);
}

async function review(page: Page) {
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });
}

test("write, check and save architecture rules; breaches become issues", async ({ page }) => {
  test.setTimeout(360_000);
  await signIn(page);
  await createProject(page, `P10 rules ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_GRAPH_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  const upload = page.url();
  await review(page);
  // Architecture smells (P10 slice 3) run on every review; this fixture has none to report.
  await expect(page.getByTestId("check-smells")).toContainText("Architecture smells");

  await page.goto(`/#/projects/${project}?tab=architecture`);
  const card = page.getByTestId("architecture-rules");
  await expect(card.getByTestId("rules-empty")).toBeVisible();
  await card.getByRole("button", { name: "Write rules" }).click();
  await expect(card.getByTestId("code-editor")).toContainText("layers:");

  // Invalid rules are refused with the reason and where it is.
  await replaceText(page, "layers:\n  - name: core\n    match: ['com..acme']\n");
  await card.getByRole("button", { name: "Save rules" }).click();
  await expect(card.getByTestId("rules-problems")).toContainText("empty name part");

  // Check the rules on the latest upload before saving.
  await replaceText(page, RULES);
  await card.getByRole("button", { name: "Check on the latest upload" }).click();
  const preview = card.getByTestId("rules-check");
  await expect(preview).toContainText("would be reported on the latest upload");
  await expect(card.getByTestId("rules-check-breaches")).toContainText(
    "Layer app must not use core",
  );
  await page.screenshot({ path: "test-results/screens/p10-rules-editor.png", fullPage: true });

  await card.getByLabel("What changed (optional)").fill("First layers");
  await card.getByRole("button", { name: "Save rules" }).click();
  const summary = card.getByTestId("rules-summary");
  await expect(summary).toContainText("Version 1");
  await expect(summary).toContainText("First layers");
  await expect(summary.getByRole("list", { name: "Layers, top to bottom" })).toContainText("core");
  await expect(summary.getByTestId("rules-check")).toContainText("in the latest upload");
  await page.screenshot({ path: "test-results/screens/p10-rules.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/p10-rules-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
  await page.screenshot({ path: "test-results/screens/p10-rules-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });

  // The next review applies the rules; breaches are tracked issues.
  await page.goto(upload);
  await review(page);
  await page.goto(`/#/projects/${project}?tab=architecture`);
  await page.getByTestId("architecture-rules").getByText("Breaches in Issues").click();
  await expect(page.getByLabel("Check")).toHaveValue("architecture");
  const issues = page.getByTestId("issues");
  await expect(issues).toContainText("Layer app must not use core");
  await page.screenshot({ path: "test-results/screens/p10-rules-issues.png", fullPage: true });
});
