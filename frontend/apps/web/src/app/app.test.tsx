import { createApiClient } from "@viewallet/api-client";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { createWebI18n } from "../i18n";
import en from "../i18n/en.json";
import vi_ from "../i18n/vi.json";
import { Providers } from "./providers";
import { createQueryClient } from "./queryClient";
import { routes } from "./router";

const okStatus = {
  status: "ok",
  version: "0.1.0",
  environment: "development",
  server_time: "2026-11-21T08:00:00Z",
  checks: { database: "ok", migrations: "ok", redis: "ok" },
};

function renderApp(fetchImpl: typeof fetch, path = "/") {
  const i18n = createWebI18n("vi");
  const apiClient = createApiClient({ baseUrl: "http://api.test", fetch: fetchImpl });
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  render(
    <Providers i18n={i18n} apiClient={apiClient} queryClient={queryClient}>
      <RouterProvider router={router} />
    </Providers>,
  );
  return { i18n, router };
}

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });

describe("web app shell", () => {
  it("shows the live system status from the API", async () => {
    renderApp(vi.fn<typeof fetch>(async () => json(okStatus)));
    expect(screen.getByRole("heading", { level: 1, name: vi_.home.title })).toBeInTheDocument();
    expect(await screen.findByText(vi_.status.overallOk)).toBeInTheDocument();
    const list = screen.getByRole("list");
    expect(within(list).getAllByText(vi_.status.ok)).toHaveLength(3);
    expect(screen.getByText("0.1.0")).toBeInTheDocument();
  });

  it("shows failing checks", async () => {
    renderApp(async () =>
      json({
        ...okStatus,
        status: "fail",
        checks: { database: "ok", migrations: "ok", redis: "fail" },
      }),
    );
    expect(await screen.findByText(vi_.status.overallFail)).toBeInTheDocument();
    expect(screen.getByText(vi_.status.fail)).toBeInTheDocument();
  });

  it("shows an error state with retry when the API is unreachable", async () => {
    const fetchMock = vi
      .fn<typeof fetch>()
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(json(okStatus));
    renderApp(fetchMock);
    expect(await screen.findByRole("alert")).toHaveTextContent(vi_.status.errorTitle);
    await userEvent.click(screen.getByRole("button", { name: vi_.status.retry }));
    expect(await screen.findByText(vi_.status.overallOk)).toBeInTheDocument();
  });

  it("switches language and remembers the choice", async () => {
    renderApp(async () => json(okStatus));
    const select = screen.getByRole("combobox", { name: vi_.language.label });
    await userEvent.selectOptions(select, "en");
    expect(
      await screen.findByRole("heading", { level: 1, name: en.home.title }),
    ).toBeInTheDocument();
    expect(localStorage.getItem("vw.locale")).toBe("en");
  });

  it("has labelled navigation landmarks and a skip link", () => {
    renderApp(async () => json(okStatus));
    expect(screen.getAllByRole("navigation", { name: vi_.nav.label }).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: vi_.app.skipToContent })).toHaveAttribute(
      "href",
      "#main",
    );
  });

  it("renders a not-found page for unknown routes", () => {
    renderApp(async () => json(okStatus), "/khong-ton-tai");
    expect(screen.getByText(vi_.notFound.title)).toBeInTheDocument();
  });

  it("serves the style guide in development", () => {
    renderApp(async () => json(okStatus), "/design");
    expect(screen.getByRole("heading", { level: 1, name: /Design system/ })).toBeInTheDocument();
  });
});

describe("i18n resources", () => {
  const keys = (obj: object, prefix = ""): string[] =>
    Object.entries(obj).flatMap(([k, v]) =>
      typeof v === "object" && v !== null ? keys(v as object, `${prefix}${k}.`) : [`${prefix}${k}`],
    );

  it("vi and en define the same keys", () => {
    expect(keys(en).sort()).toEqual(keys(vi_).sort());
  });
});
