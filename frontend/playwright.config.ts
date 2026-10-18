import { defineConfig, devices } from "@playwright/test";

const testDatabaseUrl =
  process.env.TEST_DATABASE_URL ??
  "postgresql+psycopg://expense_test:expense_test@127.0.0.1:55435/expense_analyzer_test";

export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["line"], ["html", { open: "never" }]] : "line",
  use: {
    baseURL: "http://127.0.0.1:3100",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: [
    {
      command: `cd ../backend && DATABASE_URL="${testDatabaseUrl}" FRONTEND_ORIGIN="http://127.0.0.1:3100" .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18000`,
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
