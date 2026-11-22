import { createBrowserRouter, type RouteObject } from "react-router";

import { RequirePermission, RequireStaff } from "../features/auth/StaffAuth";
import { AdminShell } from "../layouts/AdminShell";
import { BareLayout } from "../layouts/BareLayout";
import { AuditPage } from "../routes/AuditPage";
import { MfaSetupPage, MfaVerifyPage, StaffLoginPage } from "../routes/auth/StaffLoginPages";
import { NotFoundPage } from "../routes/NotFoundPage";
import { OverviewPage } from "../routes/OverviewPage";

export const routes: RouteObject[] = [
  {
    path: "login",
    element: (
      <BareLayout>
        <StaffLoginPage />
      </BareLayout>
    ),
  },
  {
    path: "mfa/setup",
    element: (
      <BareLayout>
        <MfaSetupPage />
      </BareLayout>
    ),
  },
  {
    path: "mfa/verify",
    element: (
      <BareLayout>
        <MfaVerifyPage />
      </BareLayout>
    ),
  },
  {
    element: <AdminShell />,
    children: [
      {
        element: <RequireStaff />,
        children: [
          { index: true, element: <OverviewPage /> },
          {
            path: "audit",
            element: (
              <RequirePermission permission="audit:read:any">
                <AuditPage />
              </RequirePermission>
            ),
          },
        ],
      },
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
