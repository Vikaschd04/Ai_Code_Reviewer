import { defineConfig } from "@playwright/test";

// Run through `make test-e2e` (crp-dev test-e2e), which starts an isolated PostgreSQL, Temporal,
// API, worker and built web app on free loopback ports and exports CRP_E2E_BASE_URL.
const baseURL = process.env.CRP_E2E_BASE_URL;
if (!baseURL) {
  throw new Error("CRP_E2E_BASE_URL is not set; run E2E tests with `make test-e2e`.");
}

// Default to the locally installed Google Chrome to avoid a browser download; set
// CRP_E2E_CHANNEL="" to use Playwright's bundled Chromium (`pnpm exec playwright install chromium`).
const channel = process.env.CRP_E2E_CHANNEL ?? "chrome";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL,
    ...(channel ? { channel } : {}),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});
