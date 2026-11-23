import { expect, test } from "@playwright/test";

import { createStaff, PASSWORD, totp, uniqueEmail } from "./support";

test("staff signs in, enrolls TOTP, saves recovery codes and reads the audit log", async ({
  page,
}) => {
  const email = uniqueEmail("root");
  createStaff(email, ["SUPER_ADMIN"]);

  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Email nhân viên").fill(email);
  await page.getByLabel("Mật khẩu", { exact: true }).fill(PASSWORD);
  await page.getByRole("button", { name: "Tiếp tục" }).click();

  await expect(
    page.getByRole("heading", { level: 1, name: "Thiết lập xác thực hai lớp" }),
  ).toBeVisible();
  await expect(page.getByRole("img", { name: /Mã QR/ })).toBeVisible();
  await expect(page.locator("code").first()).toHaveText(/^[A-Z2-7]{16,}$/);
  const secret = (await page.locator("code").first().textContent())?.trim() ?? "";
  expect(secret).toMatch(/^[A-Z2-7]{16,}$/);
  await page.getByLabel("Mã 6 số từ ứng dụng xác thực").fill(totp(secret));
  await page.getByRole("button", { name: "Bật xác thực hai lớp" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Mã khôi phục" })).toBeVisible();
  await expect(page.getByRole("list", { name: "Mã khôi phục" }).getByRole("listitem")).toHaveCount(
    10,
  );
  await page.getByRole("button", { name: "Tôi đã lưu mã, tiếp tục" }).click();

  await expect(page.getByRole("heading", { level: 1, name: "Tổng quan hệ thống" })).toBeVisible();
  await expect(page.getByText("Mọi dịch vụ hoạt động bình thường")).toBeVisible();
  await expect(page.getByText("Môi trường: DEVELOPMENT")).toBeVisible();

  await page.getByRole("link", { name: "Nhật ký audit" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Nhật ký audit" })).toBeVisible();
  await expect(page.getByText("identity.mfa_enrolled").first()).toBeVisible();
  await page.getByRole("button", { name: "Kiểm tra chuỗi hash" }).click();
  await expect(page.getByText(/Chuỗi hợp lệ/)).toBeVisible();

  const response = await page.request.get("/");
  expect(response.headers()["x-robots-tag"]).toBe("noindex, nofollow");

  await page.getByRole("button", { name: "Đăng xuất" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Đăng nhập quản trị" })).toBeVisible();
});
