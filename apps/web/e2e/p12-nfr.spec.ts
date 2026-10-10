import { expect, test } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// P12 (ADR 0024): after a review, Insights shows every NFR checkpoint by area with what the code
// and its configuration show (file and line), and the team can mark a mechanism that lives
// outside the code as handled elsewhere. The archive is synthetic.

test("NFR checkpoints: evidence by area and handled elsewhere", async ({ page }) => {
  test.setTimeout(300_000);
  await signIn(page);
  await createProject(page, `P12 NFR ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_NFR_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByTestId("snapshot-link").click();
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete|Complete/, {
    timeout: 150_000,
  });

  // Old questionnaire links open the checkpoints.
  await page.goto(`/#/projects/${project}?tab=nfr`);
  await expect(page.getByRole("link", { name: "NFR checkpoints" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  const all = page.getByTestId("all-checkpoints");
  const health = all.getByTestId("checkpoint-reliability.health");
  await expect(health).toHaveAttribute("data-status", "in_place");
  await health.locator("summary").click();
  const evidence = health.getByTestId("checkpoint-evidence");
  await expect(evidence).toContainText("Health and readiness endpoints");
  await expect(evidence).toContainText("pom.xml:9");
  await expect(evidence).toContainText("Kubernetes health probes");
  await expect(evidence).toContainText("deploy/k8s/deployment.yaml:20");
  await expect(all.getByTestId("area-experience")).toContainText("Not applicable here");
  await page.screenshot({ path: "test-results/screens/p12-nfr.png", fullPage: true });

  // Monitoring runs outside this code: the team says so, and it counts as their statement.
  const monitoring = page.getByTestId("needs-work").getByTestId("checkpoint-operations.monitoring");
  await expect(monitoring).toHaveAttribute("data-status", "missing");
  await monitoring.getByTestId("checkpoint-mark-handled").click();
  await monitoring.getByLabel("How is it handled?").fill("Prometheus scrapes the platform sidecar");
  await monitoring.getByRole("button", { name: "Save" }).click();
  const row = all.getByTestId("checkpoint-operations.monitoring");
  await expect(row).toHaveAttribute("data-status", "handled");
  await row.locator("summary").click();
  await expect(row).toContainText("Prometheus scrapes the platform sidecar");
  await expect(
    page.getByTestId("needs-work").getByTestId("checkpoint-operations.monitoring"),
  ).toHaveCount(0);

  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/p12-nfr-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      "document.documentElement.scrollWidth - document.documentElement.clientWidth",
    ),
  ).toBe(0);
  await page.screenshot({ path: "test-results/screens/p12-nfr-mobile.png", fullPage: true });

  await row.getByRole("button", { name: "Undo" }).click();
  await expect(row).toHaveAttribute("data-status", "missing");
});
