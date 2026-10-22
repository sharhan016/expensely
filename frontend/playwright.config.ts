import path from "node:path";
import { defineConfig, devices } from "@playwright/test";

const restartMarker = path.resolve(process.cwd(), "tests/e2e/.auth/restart-backend");

const testDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  "postgresql+psycopg://expense_test:expense_test@127.0.0.1:55435/expense_analyzer_test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  // One backend and one dev server with a shared signed-in account: run specs one at a time.
  workers: 1,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  globalSetup: "./tests/e2e/global-setup.ts",
  projects: [
    // Registration and import flows that create their own throwaway users.
    { name: "chromium", testIgnore: /signed-in\.spec\.ts/, use: { ...devices["Desktop Chrome"] } },
    // Specs that start already signed in as the shared account from the git-ignored .env.e2e.
    {
      name: "signed-in",
      testMatch: /signed-in\.spec\.ts/,
      use: { ...devices["Desktop Chrome"], storageState: "tests/e2e/.auth/user.json" },
    },
  ],
  webServer: [
    {
      command: `cd ../backend && DATABASE_URL="${testDatabaseUrl}" FRONTEND_ORIGIN="http://127.0.0.1:3100" E2E_RESTART_MARKER="${restartMarker}" .venv/bin/python scripts/e2e_supervisor.py`,
      url: "http://127.0.0.1:18000/health",
      reuseExistingServer: false,
      timeout: 30_000,
    },
    {
      command:
        'NEXT_DIST_DIR=".next-e2e" API_BASE_URL="http://127.0.0.1:18000" npm run dev -- --hostname 127.0.0.1 --port 3100',
      url: "http://127.0.0.1:3100/login",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
