import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// The simplified project (docs/PRODUCT_SIMPLIFICATION.md): six tabs, health and next steps on the
// Overview, recommendations from the tools in Insights leading straight to a focused fix
// workspace, and an AI improvement plan whose unsupported suggestions are removed. The model is
// the labelled fake provider started by `make test-e2e`.

function sections(page: Page) {
  return page.getByRole("navigation", { name: "Sections" });
}

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
}

test("insights: health, recommendations, start fixing and a checked AI plan", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `Insights ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await expect(sections(page).getByRole("link")).toHaveText([
    "Overview",
    "Issues",
    "Insights",
    "Fixes",
    "Uploads",
    "Settings",
  ]);
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_NFR_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  // Overview: health by area and the next steps.
  await page.goto(`/#/projects/${project}`);
  const health = page.getByTestId("health-summary");
  await expect(health.getByTestId("next-steps")).toContainText(
    "Untrusted input can reach dangerous calls",
  );
  await expect(health.locator('[data-area="security"]')).toHaveAttribute(
    "data-state",
    /attention|improve/,
  );
  await page.screenshot({ path: "test-results/screens/insights-overview.png", fullPage: true });

  // Insights: recommendations with guided steps; "Start fixing" opens a focused workspace.
  await sections(page).getByRole("link", { name: "Insights" }).click();
  const injection = page.getByTestId("insight-security.injection");
  await expect(injection).toContainText("How to improve it");
  await expect(injection).toContainText("parameterised queries");
  await expect(page.getByTestId("insight-operations.diagnostics")).toContainText(
    "Nothing in the uploaded code or configuration shows it.",
  );
  await page.screenshot({ path: "test-results/screens/insights.png", fullPage: true });
  await injection.getByTestId("insight-start-fixing").click();
  await expect(page).toHaveURL(/#\/workspaces\/.*issues=/);
  await expect(page.getByTestId("workspace-focus")).toContainText(
    "Showing the issues of one recommendation",
  );
  await expect(page.getByTestId("workspace-issues").locator("tbody tr")).toHaveCount(2);
  await expect(page.getByTestId("workspace-issues")).toContainText("cart.js");

  // An admin switches AI on in Settings; the plan keeps only steps with evidence.
  await page.goto(`/#/projects/${project}?tab=settings`);
  await page.getByLabel(/I am allowed to share this project's code/).check();
  await page.getByRole("button", { name: "Switch on AI review" }).click();
  await expect(page.getByTestId("ai-policy")).toHaveAttribute("data-enabled", "true");
  await sections(page).getByRole("link", { name: "Insights" }).click();
  await page.getByTestId("advisor-request").click();
  const steps = page.getByTestId("advisor-steps");
  await expect(steps).toContainText("[Test provider] Start with:", { timeout: 120_000 });
  await expect(steps.locator("> li")).toHaveCount(2);
  const rejected = page.getByTestId("advisor-rejected");
  await expect(rejected).toContainText("2 suggestions removed for lack of evidence");
  await rejected.locator("summary").click();
  await expect(rejected).toContainText("uses numbers not in the cited evidence: 4242");
  await page.getByTestId("advisor").scrollIntoViewIfNeeded();
  await page.screenshot({ path: "test-results/screens/insights-plan.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/insights-plan-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/insights-mobile.png", fullPage: true });
});
