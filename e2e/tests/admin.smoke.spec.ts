import { expect, test } from "@playwright/test";

test("admin overview shows service health and environment", async ({ page, request }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Tổng quan hệ thống" })).toBeVisible();
  await expect(page.getByText("Mọi dịch vụ hoạt động bình thường")).toBeVisible();
  await expect(page.getByText("Môi trường: DEVELOPMENT")).toBeVisible();

  const response = await request.get("/");
  expect(response.headers()["x-robots-tag"]).toBe("noindex, nofollow");
});
