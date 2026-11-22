import { createBrowserRouter, type RouteObject } from "react-router";

import { APPROVER_PERMISSIONS } from "../features/auth/permissions";
import { RequirePermission, RequireStaff } from "../features/auth/StaffAuth";
import { AdminShell } from "../layouts/AdminShell";
import { BareLayout } from "../layouts/BareLayout";
import { AuditPage } from "../routes/AuditPage";
import {
  AdjustmentsPage,
  ApprovalsPage,
  ReconciliationPage,
  WalletsPage,
} from "../routes/FinancePages";
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
            path: "wallets",
            element: (
              <RequirePermission permission="user:read:any">
                <WalletsPage />
              </RequirePermission>
            ),
          },
          {
            path: "adjustments",
            element: (
              <RequirePermission permission="adjustment:propose:any">
                <AdjustmentsPage />
              </RequirePermission>
            ),
          },
          {
            path: "approvals",
            element: (
              <RequirePermission permission={APPROVER_PERMISSIONS}>
                <ApprovalsPage />
              </RequirePermission>
            ),
          },
          {
            path: "reconciliation",
            element: (
              <RequirePermission permission="reconciliation:manage:any">
                <ReconciliationPage />
              </RequirePermission>
            ),
          },
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
