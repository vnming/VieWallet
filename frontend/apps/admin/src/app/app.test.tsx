import { createApiClient } from "@viewallet/api-client";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it } from "vitest";

import { createAdminI18n } from "../i18n";
import en from "../i18n/en.json";
import vi from "../i18n/vi.json";
import { Providers } from "./providers";
import { createQueryClient } from "./queryClient";
import { routes } from "./router";

const status = {
  status: "ok",
  version: "0.1.0",
  environment: "development",
  server_time: "2026-11-21T08:00:00Z",
  checks: { database: "ok", migrations: "ok", redis: "ok" },
};

function renderAdmin(path = "/") {
  const apiClient = createApiClient({
    baseUrl: "http://api.test",
    fetch: async () =>
      new Response(JSON.stringify(status), { headers: { "content-type": "application/json" } }),
  });
  const queryClient = createQueryClient();
  render(
    <Providers i18n={createAdminI18n("vi")} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={createMemoryRouter(routes, { initialEntries: [path] })} />
    </Providers>,
  );
}

describe("admin app shell", () => {
  it("shows service health and the environment badge", async () => {
    renderAdmin();
    expect(screen.getByRole("heading", { level: 1, name: vi.overview.title })).toBeInTheDocument();
    expect(await screen.findByText(vi.status.overallOk)).toBeInTheDocument();
    expect(screen.getByText(`${vi.app.environment}: DEVELOPMENT`)).toBeInTheDocument();
  });

  it("renders not found for unknown routes", () => {
    renderAdmin("/x");
    expect(screen.getByText(vi.notFound.title)).toBeInTheDocument();
  });

  it("vi and en define the same keys", () => {
    const keys = (obj: object, prefix = ""): string[] =>
      Object.entries(obj).flatMap(([k, v]) =>
        typeof v === "object" && v !== null
          ? keys(v as object, `${prefix}${k}.`)
          : [`${prefix}${k}`],
      );
    expect(keys(en).sort()).toEqual(keys(vi).sort());
  });
});
