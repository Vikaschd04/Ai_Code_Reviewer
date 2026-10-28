import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// A code tour for newcomers (ADD-A7): Insights → Code tour gives a one-page brief of the newest
// reviewed upload — what it is made of, its parts, where to start reading, entry points and the
// files with the most open issues — built from the review only (no AI).

async function noOverflow(page: Page) {
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
}

test("code tour: parts, where to start reading and what to handle with care", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `Tour ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.goto(`/#/projects/${project}?tab=insights&view=tour`);
  await expect(page.getByText("No review yet")).toBeVisible();
  await page.goto(`/#/projects/${project}?tab=uploads`);
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_GRAPH_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  await page.goto(`/#/projects/${project}?tab=insights&view=tour`);
  const tour = page.getByTestId("code-tour");
  await expect(tour.getByTestId("tour-made-of")).toContainText("java");
  await expect(tour.getByTestId("tour-parts")).toContainText("com.acme.core");
  await expect(tour.getByTestId("tour-start-here")).toContainText("Entity.java");
  await expect(tour).toContainText("nothing here is written by AI");
  await page.screenshot({ path: "test-results/screens/tour.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/tour-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/tour-mobile.png", fullPage: true });
});
