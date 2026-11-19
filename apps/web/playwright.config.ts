import { defineConfig, devices } from "@playwright/test";

// The demo flow runs against a running stack: `make api` (FastAPI on :8000) and `npm run dev` (Next on :3000),
// or BASE_URL pointing at a deployment.
export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  expect: { timeout: 30_000 },
  fullyParallel: false,
  retries: process.env.CI ? 1 : 0,
  // keep artefacts out of apps/web: the dev server watches it and would hot-reload pages mid-test
  outputDir: "../../.playwright/test-results",
  reporter: [["list"], ["html", { open: "never", outputFolder: "../../.playwright/report" }]],
  use: { baseURL: process.env.BASE_URL ?? "http://localhost:3000", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile", use: { ...devices["Pixel 7"] } },
  ],
});
