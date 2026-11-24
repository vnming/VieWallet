import AxeBuilder from "@axe-core/playwright";
import { type APIRequestContext, type Page, expect, test } from "@playwright/test";

import { createCustomer, PASSWORD, PIN, resetRateLimits, uniqueEmail } from "./support";

const CSRF = { "X-VW-CSRF": "1" };

test.describe.configure({ timeout: 120_000 });
test.beforeEach(() => {
  resetRateLimits();
});

async function signedIn(request: APIRequestContext): Promise<string> {
  const email = uniqueEmail("hub");
  createCustomer(email, "Phạm Minh Châu");
  const login = await request.post("/api/v1/auth/login", {
    headers: CSRF,
    data: { email, password: PASSWORD },
  });
  expect(login.status()).toBe(200);
  await request.post("/api/v1/me/pin", { headers: CSRF, data: { password: PASSWORD, pin: PIN } });
  return email;
}

/** Serious/critical axe violations, colour contrast included (a real browser renders CSS). */
async function expectAccessible(page: Page): Promise<void> {
  const result = await new AxeBuilder({ page }).analyze();
  const serious = result.violations
    .filter((v) => v.impact === "serious" || v.impact === "critical")
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).join(", ")}`);
  expect(serious).toEqual([]);
}

test("help center works signed out and has no serious accessibility issues", async ({ page }) => {
  await page.goto("/help");
  await expect(page.getByRole("heading", { level: 1, name: "Trợ giúp" })).toBeVisible();
  await page.getByLabel("Tìm bài viết").fill("rut tien");
  await page.getByRole("link", { name: /Rút tiền về ngân hàng/ }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Rút tiền về ngân hàng" })).toBeVisible();
  await expectAccessible(page);
});

test("settings, a support ticket about a transaction, history export", async ({ page }) => {
  await signedIn(page.request);

  // Theme: dark applies at once and stays after a reload (stored on the account).
  await page.goto("/settings");
  await page.getByRole("radio", { name: "Tối" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expectAccessible(page);
  await page.getByRole("radio", { name: "Theo hệ thống" }).click();
  await expect(page.locator("html")).not.toHaveAttribute("data-theme", /.+/);

  // Privacy mode hides the balance on the home page.
  await page.getByLabel("Ẩn số dư và số tiền").check();
  await page.goto("/");
  await expect(page.getByRole("region", { name: "Số dư khả dụng" }).getByText(/₫/)).toHaveCount(0);
  await page.goto("/settings");
  await page.getByLabel("Ẩn số dư và số tiền").uncheck();

  // Support ticket.
  await page.goto("/support/tickets?new=1");
  await page.getByLabel("Tiêu đề").fill("Câu hỏi về hạn mức");
  await page.getByLabel("Nội dung").fill("Hạn mức chuyển tiền của tôi là bao nhiêu?");
  await page.getByRole("button", { name: "Gửi" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Câu hỏi về hạn mức" })).toBeVisible();
  await expect(page.getByText("Đang xử lý")).toBeVisible();
  await expectAccessible(page);

  // History: filters and a CSV export (an empty history still exports a header row).
  await page.goto("/history");
  await page.getByLabel("Chiều tiền").selectOption("OUT");
  await expect(page.getByText("Không có giao dịch phù hợp bộ lọc", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "Tạo tệp CSV" }).click();
  const link = page.getByRole("link", { name: /Tải tệp/ });
  await expect(link).toBeVisible({ timeout: 30_000 });
  const download = await page.request.get((await link.getAttribute("href")) ?? "");
  expect(download.headers()["content-type"]).toContain("text/csv");
  expect((await download.text()).replace(/^﻿/, "")).toMatch(/^Mã giao dịch,Thời gian/);
  await expectAccessible(page);

  await page.goto("/account");
  await expectAccessible(page);
});
