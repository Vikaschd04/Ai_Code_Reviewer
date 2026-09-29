import { readFileSync } from "node:fs";

import { expect, type Page } from "@playwright/test";

export function env(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set; run the E2E suite with \`make test-e2e\`.`);
  return value;
}

export const token = readFileSync(env("CRP_E2E_TOKEN_FILE"), "utf8").trim();

export async function signIn(page: Page) {
  await page.goto("/");
  await page.getByLabel("Access token").fill(token);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Overview" })).toBeVisible();
}

export async function createProject(page: Page, name: string) {
  await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: "Projects" })
    .click();
  await page.getByLabel("Name").fill(name);
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("heading", { level: 1, name })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Upload your code" })).toBeVisible();
}
