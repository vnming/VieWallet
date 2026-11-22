import { createBrowserRouter, type RouteObject } from "react-router";

import { RequireAuth } from "../features/auth/RequireAuth";
import { AppShell } from "../layouts/AppShell";
import { SecurityPage } from "../routes/account/SecurityPage";
import { SetPinPage } from "../routes/account/SetPinPage";
import { LoginPage } from "../routes/auth/LoginPage";
import { ForgotPasswordPage, ResetPasswordPage } from "../routes/auth/PasswordResetPages";
import { RegisterPage } from "../routes/auth/RegisterPage";
import { VerifyEmailPage } from "../routes/auth/VerifyEmailPage";
import { DesignPage } from "../routes/DesignPage";
import { HomePage } from "../routes/HomePage";
import { NotFoundPage } from "../routes/NotFoundPage";
import { HistoryPage, TransactionDetailPage, WalletPage } from "../routes/wallet/WalletPages";

export const routes: RouteObject[] = [
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "login", element: <LoginPage /> },
      { path: "register", element: <RegisterPage /> },
      { path: "verify-email", element: <VerifyEmailPage /> },
      { path: "forgot-password", element: <ForgotPasswordPage /> },
      { path: "reset-password", element: <ResetPasswordPage /> },
      {
        element: <RequireAuth />,
        children: [
          { path: "onboarding/pin", element: <SetPinPage /> },
          { path: "account/security", element: <SecurityPage /> },
          { path: "wallet", element: <WalletPage /> },
          { path: "history", element: <HistoryPage /> },
          { path: "history/:reference", element: <TransactionDetailPage /> },
        ],
      },
      // Style guide with sample data: development builds only.
      ...(import.meta.env.DEV ? [{ path: "design", element: <DesignPage /> }] : []),
      { path: "*", element: <NotFoundPage /> },
    ],
  },
];

export function createRouter() {
  return createBrowserRouter(routes);
}
