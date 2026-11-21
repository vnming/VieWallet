import { createBrowserRouter, type RouteObject } from "react-router";

import { AppShell } from "../layouts/AppShell";
import { DesignPage } from "../routes/DesignPage";
import { HomePage } from "../routes/HomePage";
import { NotFoundPage } from "../routes/NotFoundPage";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      // Style guide with sample data: development builds only.
      ...(import.meta.env.DEV ? [{ path: "design", element: <DesignPage /> }] : []),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
