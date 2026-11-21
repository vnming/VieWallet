import { expect, test } from "@playwright/test";

test.describe("customer app smoke", () => {
  test("home shows live system status from the backend", async ({ page }) => {
    await page.goto("/");
    await expect(
      page.getByRole("heading", { level: 1, name: "Chào mừng đến VieWallet" }),
    ).toBeVisible();
    await expect(page.getByText("Mọi dịch vụ hoạt động bình thường")).toBeVisible();
    await expect(page.getByText("Hoạt động", { exact: true })).toHaveCount(3);
  });

  test("language switch changes the UI to English", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("combobox", { name: "Ngôn ngữ" }).selectOption("en");
    await expect(
      page.getByRole("heading", { level: 1, name: "Welcome to VieWallet" }),
    ).toBeVisible();
    await page.reload();
    await expect(
      page.getByRole("heading", { level: 1, name: "Welcome to VieWallet" }),
    ).toBeVisible();
  });

  test("unknown routes fall back to the SPA not-found page", async ({ page }) => {
    await page.goto("/khong-ton-tai");
    await expect(page.getByText("Không tìm thấy trang")).toBeVisible();
  });

  test("security headers are set on the app and the proxied API", async ({ request }) => {
    const app = await request.get("/");
    expect(app.headers()["content-security-policy"]).toContain("default-src 'self'");
    expect(app.headers()["x-frame-options"]).toBe("DENY");

    const api = await request.get("/api/v1/system/status");
    expect(api.ok()).toBeTruthy();
    expect(api.headers()["x-request-id"]).toBeTruthy();
    expect(api.headers()["cache-control"]).toBe("no-store");
  });

  test("API errors use Problem Details", async ({ request }) => {
    const response = await request.get("/api/v1/does-not-exist", {
      headers: { "Accept-Language": "en" },
    });
    expect(response.status()).toBe(404);
    expect(response.headers()["content-type"]).toContain("application/problem+json");
    const body = await response.json();
    expect(body).toMatchObject({
      code: "NOT_FOUND",
      title: "Not found",
      status: 404,
    });
  });
});
