import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 3 browser flows on the real stack: the project's AI sharing switch, a question, a file
// review and a second opinion on a finding. The model is the labelled fake provider started by
// `make test-e2e` (crp_devtools.testing.fake_ai): it cites what refactorX's tools return, so
// these tests check the pipeline and UI, never answer quality.

async function openAiTab(page: Page) {
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "AI review" })
    .click();
}

test("AI review: opt-in, question, file review and finding second opinion", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await expect(page.locator(".sidebar-footer")).toContainText("only for AI review");
  await createProject(page, `P03 AI ${Date.now()}`);
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_SEEDED_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete/, {
    timeout: 120_000,
  });
  const review = page.url();

  // Off by default; switching on needs an explicit confirmation.
  await page.getByTestId("project-link").click();
  await openAiTab(page);
  const policy = page.getByTestId("ai-policy");
  await expect(policy).toHaveAttribute("data-enabled", "false");
  const enable = page.getByRole("button", { name: "Switch on AI review" });
  await expect(enable).toBeDisabled();
  await page.screenshot({ path: "test-results/screens/p03-ai-off.png", fullPage: true });
  await page.getByLabel(/I am allowed to share this project's code/).check();
  await enable.click();
  await expect(policy).toHaveAttribute("data-enabled", "true");

  // A question: the answer cites a line that the citation check confirms.
  await page.getByLabel("Your question").fill("Where does InvoiceService compare the status?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page).toHaveURL(/#\/ai-runs\//);
  const answer = page.getByTestId("ai-answer");
  await expect(answer).toContainText("[Test provider]", { timeout: 60_000 });
  await expect(answer.locator("[data-evidence]").first()).toHaveText("Checked against your code");
  await expect(answer.getByTestId("ai-anchors")).toContainText("InvoiceService.java");
  await expect(page.locator("[data-anchor-status]").first()).toHaveText(/Matches the code/);
  await page.getByTestId("ai-technical").getByText("Technical details").click();
  await expect(page.getByTestId("ai-technical")).toContainText("refactorx-e2e-fake");
  await page.screenshot({ path: "test-results/screens/p03-ai-answer.png", fullPage: true });

  // A file review of one chosen file.
  await page.getByRole("link", { name: "Back to AI review" }).click();
  await page.getByLabel("Find a file to review").fill("cart");
  await page.getByRole("button", { name: "web/src/cart.ts" }).click();
  await expect(page.getByRole("list", { name: "Files to review" })).toContainText("cart.ts");
  await page.getByRole("button", { name: "Review file" }).click();
  await expect(page.getByRole("heading", { name: "Problems found (1)" })).toBeVisible({
    timeout: 60_000,
  });
  const finding = page.getByTestId("ai-finding").first();
  await expect(finding).toContainText("Test provider note on cart.ts");
  await expect(finding.locator("[data-evidence]")).toHaveText("Checked against your code");
  await page.screenshot({ path: "test-results/screens/p03-ai-review.png", fullPage: true });

  // A second opinion on a finding from the automatic review.
  await page.goto(review);
  await page.getByLabel("Search findings by title or path").fill("Strings compared");
  await page.getByTestId("finding-row").first().click();
  const check = page.getByTestId("ai-check");
  await expect(check).toBeVisible();
  await check.getByRole("button", { name: "Ask AI to check this" }).click();
  await expect(page.getByTestId("ai-verdict")).toHaveText("Not sure", { timeout: 60_000 });
  await expect(page.getByTestId("ai-anchors").first()).toContainText("InvoiceService.java");
  await page.screenshot({ path: "test-results/screens/p03-ai-verdict.png", fullPage: true });

  // All three runs are listed on the project.
  await page.getByRole("link", { name: "Back to AI review" }).click();
  await expect(page.getByTestId("ai-runs").locator("tbody tr")).toHaveCount(3);
  await page.screenshot({ path: "test-results/screens/p03-ai-tab.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/p03-ai-tab-dark.png", fullPage: true });
  await page.getByTestId("ai-runs").getByRole("link").first().click();
  await expect(page.getByTestId("ai-verdict")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/p03-ai-verdict-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();

  // Mobile width: no horizontal page scroll on the run page or the AI tab.
  await page.setViewportSize({ width: 390, height: 844 });
  const overflow = (): Promise<unknown> =>
    page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth");
  expect(await overflow()).toBe(0);
  await page.screenshot({ path: "test-results/screens/p03-ai-verdict-mobile.png", fullPage: true });
  await page.getByRole("link", { name: "Back to AI review" }).click();
  await expect(page.getByTestId("ai-runs")).toBeVisible();
  expect(await overflow()).toBe(0);
  await page.screenshot({ path: "test-results/screens/p03-ai-tab-mobile.png", fullPage: true });
});
