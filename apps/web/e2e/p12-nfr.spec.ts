import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// P12: after a review, the NFR questionnaire answers with evidence from the code and its
// configuration (with file and line), and the team's targets and answers complete it. The
// archive is synthetic.

function question(page: Page, id: string) {
  return page.getByTestId(`nfr-q-${id}`);
}

async function status(page: Page, id: string) {
  return question(page, id).locator("summary [data-status]").getAttribute("data-status");
}

test("NFR readiness: evidence, targets, answers and not applicable", async ({ page }) => {
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

  await page.goto(`/#/projects/${project}?tab=nfr`);
  await expect(page.getByTestId("nfr-basis")).toContainText("Evidence from the latest review");
  const recovery = question(page, "recoverability.recovery-time");
  await recovery.locator("summary").click();
  await expect(recovery.getByTestId("nfr-evidence")).toContainText(
    "Health and readiness endpoints",
  );
  await expect(recovery.getByTestId("nfr-evidence")).toContainText("pom.xml:9");
  await expect(recovery.getByTestId("nfr-evidence")).toContainText("Kubernetes health probes");
  await expect(recovery.getByTestId("nfr-evidence")).toContainText("deploy/k8s/deployment.yaml:20");
  expect(await status(page, "recoverability.recovery-time")).toBe("needs_input");
  expect(await status(page, "reliability.consistency")).toBe("needs_work");
  expect(await status(page, "portability.platforms")).toBe("evidence");
  await page.screenshot({ path: "test-results/screens/p12-nfr.png", fullPage: true });

  // The team's targets turn evidence into answers.
  await page.getByLabel("Recovery time objective (RTO) (min)").fill("30");
  await page.getByLabel("Availability target (%)").fill("99.9");
  await page.getByRole("button", { name: "Save targets" }).click();
  await expect.poll(() => status(page, "recoverability.recovery-time")).toBe("evidence");
  expect(await status(page, "availability.continuous")).toBe("evidence");

  const cost = question(page, "recoverability.cost");
  await cost.locator("summary").click();
  await cost.getByLabel("Your team's answer").fill("About 20k EUR per hour of downtime.");
  await cost.getByRole("button", { name: "Save answer" }).click();
  await expect.poll(() => status(page, "recoverability.cost")).toBe("answered");

  const simplicity = question(page, "usability.simplicity");
  await simplicity.locator("summary").click();
  await simplicity.getByLabel("Does not apply to this system").check();
  await simplicity.getByLabel("Why it does not apply").fill("Back-office API without a UI");
  await simplicity.getByRole("button", { name: "Save answer" }).click();
  await expect.poll(() => status(page, "usability.simplicity")).toBe("not_applicable");
  await expect(page.getByRole("link", { name: "Spreadsheet (CSV)" })).toHaveAttribute(
    "href",
    `/v1/projects/${project}/nfr/export?format=csv`,
  );

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
});
