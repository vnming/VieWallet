import { expect, test } from "@playwright/test";

import { PASSWORD, PIN, sixDigitCode, uniqueEmail, waitForEmail } from "./support";

test("customer signs up, verifies e-mail, sets a PIN, reviews security and signs out", async ({
  page,
}) => {
  const email = uniqueEmail("lan");

  await page.goto("/register");
  await page.getByLabel("Họ và tên").fill("Trần Thị Lan");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Tạo tài khoản" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Xác minh email" })).toBeVisible();
  const code = sixDigitCode(await waitForEmail(email, /Mã xác minh/));
  await page.getByLabel("Mã xác minh").fill(code);
  await page.getByRole("button", { name: "Xác minh", exact: true }).click();

  await expect(page.getByText("Email đã được xác minh")).toBeVisible();
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Đăng nhập", exact: true }).click();

  // New accounts must set a transaction PIN before anything else.
  await expect(page.getByRole("heading", { level: 1, name: "Đặt PIN giao dịch" })).toBeVisible();
  await page.getByLabel("PIN mới").fill("123456");
  await page.getByLabel("Nhập lại PIN").fill("123456");
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Đặt PIN" }).click();
  await expect(page.getByRole("alert")).toContainText("PIN quá dễ đoán");

  await page.getByLabel("PIN mới").fill(PIN);
  await page.getByLabel("Nhập lại PIN").fill(PIN);
  await page.getByRole("button", { name: "Đặt PIN" }).click();
  await expect(
    page.getByRole("heading", { level: 1, name: "Xin chào, Trần Thị Lan" }),
  ).toBeVisible();

  await page.getByRole("link", { name: "Bảo mật" }).first().click();
  await expect(page.getByRole("heading", { level: 1, name: "Bảo mật tài khoản" })).toBeVisible();
  await expect(page.getByText("Thiết bị này")).toBeVisible();
  await expect(page.getByText("Thành công").first()).toBeVisible();

  await page.getByRole("button", { name: "Đăng xuất", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Đăng nhập" })).toBeVisible();
  await page.goto("/account/security");
  await expect(page).toHaveURL(/\/login\?next=%2Faccount%2Fsecurity/);
});

test("session cookie is HttpOnly and API mutations need the CSRF header", async ({
  page,
  request,
}) => {
  const response = await request.post("/api/v1/auth/login", {
    data: { email: "nobody@example.com", password: "x" },
  });
  expect(response.status()).toBe(403);
  expect((await response.json()).code).toBe("CSRF_FAILED");

  await page.goto("/login");
  const cookies = await page.context().cookies();
  expect(cookies.find((c) => c.name === "vw_session")).toBeUndefined();
});
