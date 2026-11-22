import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests run against the Docker Compose stack (`docker compose up -d --build`).
 * URLs can be overridden with VW_E2E_* environment variables.
 */
export default defineConfig({
  testDir: "./tests",
  globalSetup: "./global-setup.ts",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    locale: "vi-VN",
    timezoneId: "Asia/Ho_Chi_Minh",
  },
  projects: [
    {
      name: "web-mobile",
      use: {
        ...devices["Pixel 7"],
        baseURL: process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080",
      },
      testMatch: /web\..*\.spec\.ts/,
    },
    {
      name: "web-desktop",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080",
      },
      testMatch: /web\..*\.spec\.ts/,
    },
    {
      name: "admin-desktop",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: process.env.VW_E2E_ADMIN_URL ?? "http://127.0.0.1:8081",
      },
      testMatch: /admin\..*\.spec\.ts/,
    },
  ],
});
