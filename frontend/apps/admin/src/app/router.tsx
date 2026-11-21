import { createBrowserRouter, type RouteObject } from "react-router";

import { AdminShell } from "../layouts/AdminShell";
import { NotFoundPage } from "../routes/NotFoundPage";
import { OverviewPage } from "../routes/OverviewPage";

export const routes: RouteObject[] = [
  {
    element: <AdminShell />,
    children: [
      { index: true, element: <OverviewPage /> },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
