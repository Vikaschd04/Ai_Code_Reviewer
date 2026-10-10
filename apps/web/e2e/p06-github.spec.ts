import { expect, test, type APIRequestContext, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// Phase 6 browser flow on the real stack (API, worker, Temporal, analyzers). GitHub is the
// labelled fake started by `make test-e2e` (crp_devtools.testing.fake_github); its /_fake/*
// control endpoints create commits and pull requests and deliver signed webhooks.

const GITHUB = env("CRP_E2E_GITHUB_URL");
const REPO = "acme/shop";
const ORDERS_MORE = [
  "package shop;",
  "",
  "public class Orders {",
  "    public boolean paid(String status) {",
  '        return status == "PAID";',
  "    }",
  "",
  "    public boolean bulk(String kind) {",
  '        return kind == "BULK";',
  "    }",
  "}",
  "",
].join("\n");

async function control(request: APIRequestContext, path: string, data: object) {
  const response = await request.post(`${GITHUB}/_fake/${path}`, { data });
  expect(response.ok(), await response.text()).toBeTruthy();
  return (await response.json()) as Record<string, unknown>;
}

async function fakeState(request: APIRequestContext) {
  const response = await request.get(`${GITHUB}/_fake/state?repo=${REPO}`);
  return (await response.json()) as {
    check_runs: { conclusion: string; head_sha: string }[];
    comments: { body: string }[];
    pulls: { base: string; head: string }[];
  };
}

async function noOverflow(page: Page) {
  const overflow = await page.evaluate(
    "document.documentElement.scrollWidth - document.documentElement.clientWidth",
  );
  expect(overflow).toBe(0);
}

test("GitHub: verified linking, reviews, a published check and a workspace pull request", async ({
  page,
  request,
}) => {
  test.setTimeout(600_000);
  await signIn(page);

  // Link the GitHub account: the fake asks for consent and sends the browser back.
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "GitHub" })
    .click();
  await expect(page.getByTestId("github-connect")).toBeVisible();
  await page.getByTestId("github-confirm").click();
  await expect(page.getByTestId("github-link-result")).toContainText("Linked acme", {
    timeout: 30_000,
  });
  await expect(page).toHaveURL(/#\/github$/); // the one-time code is not kept in the address
  await expect(page.getByTestId("git-repository")).toContainText(REPO);
  await page.screenshot({ path: "test-results/screens/p06-github-page.png", fullPage: true });

  // Connect the repository to a project: the default branch is reviewed right away.
  await createProject(page, `P06 GitHub ${Date.now()}`);
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Settings" })
    .click();
  await page.getByTestId("github-connect-repo").getByRole("combobox").selectOption({ label: REPO });
  await page.getByRole("button", { name: "Connect and review" }).click();
  await expect(page.getByTestId("github-repository")).toContainText(REPO);
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Uploads" })
    .click();
  const rows = page.getByTestId("code-review-row");
  await expect(rows.first()).toContainText("main at", { timeout: 30_000 });
  await expect(rows.first()).toContainText("Complete", { timeout: 180_000 });

  // Allow posting a check and opening fix pull requests.
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Settings" })
    .click();
  await page.getByTestId("setting-publish_checks").check();
  await page.getByTestId("setting-publish_pull_requests").check();
  await page.getByRole("button", { name: "Save settings" }).click();
  await expect(page.getByText("Saved.")).toBeVisible();
  await page.screenshot({ path: "test-results/screens/p06-project-github.png", fullPage: true });
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Uploads" })
    .click();

  // A pull request arrives through a signed webhook.
  await control(request, "commit", {
    repo: REPO,
    branch: "feature-7",
    base: "main",
    files: { "src/main/java/shop/Orders.java": ORDERS_MORE },
    message: "bulk orders",
  });
  await control(request, "pull", {
    repo: REPO,
    number: 7,
    branch: "feature-7",
    title: "Bulk orders",
  });
  const delivered = await control(request, "deliver", {
    repo: REPO,
    event: "pull_request",
    action: "opened",
    number: 7,
  });
  expect(delivered.outcome).toBe("accepted");
  const pullRow = rows.filter({ hasText: "Pull request #7" });
  await expect(pullRow).toContainText("Complete", { timeout: 180_000 });
  await expect(pullRow).toContainText("Posted to GitHub");
  await pullRow.getByRole("link").click();
  await expect(page.getByRole("heading", { level: 1 })).toContainText("Pull request #7");
  await expect(page.getByTestId("review-new")).toContainText("2 new problems");
  await expect(page.getByTestId("review-publish")).toContainText(
    "A check and a summary comment were posted.",
  );
  await expect(page.getByTestId("review-changes")).toContainText("1 changed");
  await page.screenshot({ path: "test-results/screens/p06-review.png", fullPage: true });
  const state = await fakeState(request);
  expect(state.check_runs).toHaveLength(1);
  expect(state.check_runs[0]?.conclusion).toBe("neutral"); // never fails unless the project says so
  expect(state.comments).toHaveLength(1);
  await page.getByRole("button", { name: "Switch to dark theme" }).click();
  await page.screenshot({ path: "test-results/screens/p06-review-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/p06-review-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 800 });

  // A checked fix workspace for a finding of the default branch becomes one pull request.
  await page.getByRole("link", { name: "Back to the project" }).click();
  await page
    .getByRole("navigation", { name: "Sections" })
    .getByRole("link", { name: "Uploads" })
    .click();
  await rows.filter({ hasText: "main at" }).getByRole("link").click();
  await page
    .getByTestId("review-new")
    .getByRole("listitem")
    .filter({ hasText: "Orders.java" })
    .getByRole("link", { name: "Strings compared with ==" })
    .click();
  await page.getByTestId("workspace-open").click();
  await expect(page).toHaveURL(/#\/workspaces\//);
  await page.getByRole("link", { name: "Issues", exact: true }).click();
  await page.getByRole("button", { name: "Select all with an automatic fix" }).click();
  await page.getByTestId("workspace-fix-selected").click();
  await expect(page.getByTestId("workspace-fix-result")).toContainText(
    /Fixed \d+ issues? automatically/,
  );
  await page.getByTestId("workspace-run-check").click();
  await expect(page.getByTestId("workspace-outcomes")).toBeVisible({ timeout: 180_000 });
  await page.getByRole("link", { name: /^Changes \(\d+\)$/ }).click();
  await page.getByTestId("workspace-open-pull-request").click();
  await expect(page.getByTestId("workspace-pull-request")).toContainText(
    /Pull request #\d+ on GitHub/,
  );
  await page.screenshot({ path: "test-results/screens/p08-pull-request.png", fullPage: true });
  const delivered2 = await fakeState(request);
  expect(delivered2.pulls).toHaveLength(1);
  expect(delivered2.pulls[0]?.base).toBe("main");
});
