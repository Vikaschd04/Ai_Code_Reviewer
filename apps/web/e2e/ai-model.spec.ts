import { expect, test, type Page } from "@playwright/test";

import { createProject, env, signIn } from "./helpers";

// A project's own AI model and API key (ADR 0026): after AI review is switched on, an admin picks
// a provider and a model, and the key is tested before it is saved. A wrong key is refused and
// nothing is stored; a working key is used for this project's AI only and never shown again. The
// provider is the labelled fake started by `make test-e2e`, standing in for UnoRouter.

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

test("own AI model: choose provider and model, test the key, use it, remove it", async ({
  page,
}) => {
  test.setTimeout(480_000); // a review, then a paced free-model run
  const userKey = env("CRP_E2E_AI_USER_KEY");
  await signIn(page);
  await createProject(page, `Own model ${Date.now()}`);
  const project = page
    .url()
    .replace(/^.*#\/projects\//, "")
    .replace(/\?.*$/, "");
  await page.getByLabel("ZIP archive to upload").setInputFiles(env("CRP_E2E_SEEDED_ZIP"));
  await expect(page.getByTestId("snapshot-link")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Start review" }).click();
  await expect(page.getByTestId("stage-publish")).toContainText(/Partly complete/, {
    timeout: 120_000,
  });
  await page.getByTestId("project-link").click();

  // The model and key appear in Settings once AI review is switched on.
  await sections(page).getByRole("link", { name: "Settings" }).click();
  await expect(page.getByTestId("ai-policy")).toHaveAttribute("data-enabled", "false");
  await expect(page.getByTestId("ai-model")).toHaveCount(0);
  await page.getByLabel(/I am allowed to share this project's code/).check();
  await page.getByRole("button", { name: "Switch on AI review" }).click();
  await expect(page.getByTestId("ai-policy")).toHaveAttribute("data-enabled", "true");
  const card = page.getByTestId("ai-model");
  await expect(card).toHaveAttribute("data-configured", "false");

  // Anthropic lists the current Claude models; UnoRouter its free models.
  const providerSelect = card.getByLabel("Provider");
  const modelSelect = card.getByLabel("Model", { exact: true });
  await expect(modelSelect.locator("option").first()).toHaveText("Claude Opus 5.5 — Recommended");
  await expect(modelSelect.locator("option")).toContainText([
    "Claude Fable 5.1",
    "Claude Sonnet 5.5",
    "Claude Opus 4.8",
  ]);
  await providerSelect.selectOption({ label: "UnoRouter (free models)" });
  await expect(modelSelect.locator("option")).toHaveText([
    "glm-5.3:free — free",
    "gemini-3.6-flash:free — free",
    "deepseek-v4.1-flash:free — free",
  ]);
  await modelSelect.selectOption("glm-5.3:free");
  await expect(card).toContainText("may keep or train on them");
  await page.screenshot({ path: "test-results/screens/ai-model-form.png", fullPage: true });

  // A wrong key is refused by the provider and nothing is saved.
  const save = card.getByRole("button", { name: "Test and save" });
  await expect(save).toBeDisabled();
  await card.getByLabel("API key").fill("sk-wrong-key-0000000000");
  await save.click();
  await expect(card.getByTestId("ai-model-error")).toContainText(
    "UnoRouter did not accept this API key",
  );
  await expect(card).toHaveAttribute("data-configured", "false");

  // The working key is saved, used for this project and never shown again.
  await card.getByLabel("API key").fill(userKey);
  await save.click();
  const saved = card.getByTestId("ai-model-saved");
  await expect(saved).toContainText("In use");
  await expect(saved).toContainText("glm-5.3:free (UnoRouter)");
  await expect(saved).toContainText(`Key ending ${userKey.slice(-4)}`);
  expect(await page.content()).not.toContain(userKey);
  await expect(page.getByTestId("ai-policy-connection")).toContainText(
    "glm-5.3:free (UnoRouter, your key)",
  );
  await page.screenshot({ path: "test-results/screens/ai-model-saved.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to dark theme" }).click(); // design review
  await page.screenshot({ path: "test-results/screens/ai-model-saved-dark.png", fullPage: true });
  await page.getByRole("button", { name: "Switch to light theme" }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await noOverflow(page);
  await page.screenshot({ path: "test-results/screens/ai-model-mobile.png", fullPage: true });
  await page.setViewportSize({ width: 1280, height: 900 });

  // The project's question goes to the chosen model (free models: about one request a minute).
  await sections(page).getByRole("link", { name: "Insights" }).click();
  await page.getByLabel("Your question").fill("Where does InvoiceService compare the status?");
  await page.getByRole("button", { name: "Ask", exact: true }).click();
  await expect(page).toHaveURL(/#\/ai-runs\//);
  await expect(page.getByTestId("ai-answer")).toContainText("[Test provider]", {
    timeout: 180_000,
  });
  await page.getByTestId("ai-technical").getByText("Technical details").click();
  await expect(page.getByTestId("ai-technical")).toContainText("glm-5.3:free");

  // Removing the key returns the project to the server's AI connection.
  await page.goto(`/#/projects/${project}?tab=settings`);
  await expect(card.getByTestId("ai-model-saved")).toBeVisible();
  await card.getByRole("button", { name: "Remove key" }).click();
  await expect(card).toHaveAttribute("data-configured", "false");
  await expect(page.getByTestId("ai-policy-connection")).not.toContainText("your key");
});
