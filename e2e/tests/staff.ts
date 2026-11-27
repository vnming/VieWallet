/** Shared helpers for the Phase 8 admin E2E journeys. */
import {
  type APIRequestContext,
  type Browser,
  type Page,
  expect,
  request as playwrightRequest,
} from "@playwright/test";

import { createStaff, PASSWORD, PIN, totp, uniqueEmail } from "./support";

const WEB_URL = process.env.VW_E2E_WEB_URL ?? "http://127.0.0.1:8080";
const SIM_URL = process.env.VW_E2E_SIM_URL ?? "http://127.0.0.1:8090";
const CSRF = { "X-VW-CSRF": "1" };

export function idem(): Record<string, string> {
  return { ...CSRF, "Idempotency-Key": crypto.randomUUID() };
}

export async function signedIn(email: string): Promise<APIRequestContext> {
  const api = await playwrightRequest.newContext({ baseURL: WEB_URL });
  const login = await api.post("/api/v1/auth/login", {
    headers: CSRF,
    data: { email, password: PASSWORD },
  });
  expect(login.status()).toBe(200);
  await api.post("/api/v1/me/pin", { headers: CSRF, data: { password: PASSWORD, pin: PIN } });
  return api;
}

/** Deposit through SimBank without the UI: approve the checkout, wait for the callback. */
export async function fund(api: APIRequestContext, amount: string): Promise<void> {
  const created = (await (
    await api.post("/api/v1/deposits", { headers: idem(), data: { amount } })
  ).json()) as { reference: string; checkout_url: string };
  const sim = await playwrightRequest.newContext();
  const code = created.checkout_url.split("/").pop() ?? "";
  await sim.post(`${SIM_URL}/checkout/${code}/approve`, { maxRedirects: 0 });
  await sim.dispose();
  await expect
    .poll(
      async () =>
        ((await (await api.get(`/api/v1/deposits/${created.reference}`)).json()) as {
          status: string;
        }).status,
      { timeout: 30_000 },
    )
    .toBe("COMPLETED");
}

/** Sign in a new staff member and enroll TOTP through the admin UI. */
export async function enrolledStaff(page: Page, roles: string[]): Promise<void> {
  const email = uniqueEmail("staff");
  createStaff(email, roles);
  await page.goto("/login");
  await page.getByLabel("Email nhân viên").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Tiếp tục" }).click();
  await expect(page.locator("code").first()).toHaveText(/^[A-Z2-7]{16,}$/);
  const secret = (await page.locator("code").first().textContent())?.trim() ?? "";
  await page.getByLabel("Mã 6 số từ ứng dụng xác thực").fill(totp(secret));
  await page.getByRole("button", { name: "Bật xác thực hai lớp" }).click();
  await page.getByRole("button", { name: "Tôi đã lưu mã, tiếp tục" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Tổng quan hệ thống" })).toBeVisible();
}

export function providerCard(page: Page) {
  return page
    .locator("section")
    .filter({ has: page.getByRole("heading", { level: 2, name: "Nhà cung cấp thanh toán" }) });
}

export async function secondStaff(browser: Browser, roles: string[]): Promise<Page> {
  const context = await browser.newContext({ locale: "vi-VN" });
  const page = await context.newPage();
  await enrolledStaff(page, roles);
  return page;
}
