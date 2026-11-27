import { createBrowserRouter, type RouteObject } from "react-router";

import { APPROVER_PERMISSIONS, CONFIG_PERMISSIONS } from "../features/auth/permissions";
import { RequirePermission, RequireStaff } from "../features/auth/StaffAuth";
import { AdminShell } from "../layouts/AdminShell";
import { BareLayout } from "../layouts/BareLayout";
import { AuditPage } from "../routes/AuditPage";
import { ConfigPage, StaffPage } from "../routes/ConfigPages";
import {
  DashboardPage,
  OperationsQueuePage,
  SimulatorPage,
  SystemHealthPage,
} from "../routes/ConsolePages";
import { TicketDetailPage, TicketsPage } from "../routes/TicketPages";
import { TransactionPage, UserDetailPage, UsersPage } from "../routes/UsersPages";
import {
  AdjustmentsPage,
  ApprovalsPage,
  ReconciliationPage,
  WalletsPage,
} from "../routes/FinancePages";
import { MfaSetupPage, MfaVerifyPage, StaffLoginPage } from "../routes/auth/StaffLoginPages";
import { NotFoundPage } from "../routes/NotFoundPage";
import { KycReviewPage, RiskPage } from "../routes/RiskKycPages";

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
          { index: true, element: <DashboardPage /> },
          {
            path: "users",
            element: (
              <RequirePermission permission={"user:read:any"}>
                <UsersPage />
              </RequirePermission>
            ),
          },
          {
            path: "users/:id",
            element: (
              <RequirePermission permission={"user:read:any"}>
                <UserDetailPage />
              </RequirePermission>
            ),
          },
          {
            path: "transactions/:reference",
            element: (
              <RequirePermission permission={"transaction:read:any"}>
                <TransactionPage />
              </RequirePermission>
            ),
          },
          {
            path: "operations",
            element: (
              <RequirePermission permission={"transaction:read:any"}>
                <OperationsQueuePage />
              </RequirePermission>
            ),
          },
          {
            path: "tickets",
            element: (
              <RequirePermission permission={"ticket:manage:any"}>
                <TicketsPage />
              </RequirePermission>
            ),
          },
          {
            path: "tickets/:reference",
            element: (
              <RequirePermission permission={"ticket:manage:any"}>
                <TicketDetailPage />
              </RequirePermission>
            ),
          },
          {
            path: "config",
            element: (
              <RequirePermission permission={CONFIG_PERMISSIONS}>
                <ConfigPage />
              </RequirePermission>
            ),
          },
          {
            path: "staff",
            element: (
              <RequirePermission permission={"role:assign:any"}>
                <StaffPage />
              </RequirePermission>
            ),
          },
          {
            path: "simulator",
            element: (
              <RequirePermission permission={"simulator:manage:any"}>
                <SimulatorPage />
              </RequirePermission>
            ),
          },
          { path: "system", element: <SystemHealthPage /> },
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
            path: "kyc",
            element: (
              <RequirePermission permission="kyc:review:any">
                <KycReviewPage />
              </RequirePermission>
            ),
          },
          {
            path: "risk",
            element: (
              <RequirePermission permission="risk_hold:decide:any">
                <RiskPage />
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
