import { createBrowserRouter, type RouteObject } from "react-router";

import { RequireAuth } from "../features/auth/RequireAuth";
import { AppShell } from "../layouts/AppShell";
import {
  AccountHubPage,
  ProfilePage,
  RecipientsPage,
  SettingsPage,
} from "../routes/account/AccountPages";
import { SecurityPage } from "../routes/account/SecurityPage";
import { SetPinPage } from "../routes/account/SetPinPage";
import { LoginPage } from "../routes/auth/LoginPage";
import { ForgotPasswordPage, ResetPasswordPage } from "../routes/auth/PasswordResetPages";
import { RegisterPage } from "../routes/auth/RegisterPage";
import { VerifyEmailPage } from "../routes/auth/VerifyEmailPage";
import { DesignPage } from "../routes/DesignPage";
import { HomePage } from "../routes/HomePage";
import { KycPage, LimitsPage } from "../routes/kyc/KycPages";
import { NotFoundPage } from "../routes/NotFoundPage";
import {
  DepositPage,
  DepositResultPage,
  DestinationsPage,
  WithdrawalResultPage,
  WithdrawPage,
} from "../routes/money/MoneyPages";
import { NotificationsPage } from "../routes/NotificationsPage";
import { HelpArticlePage, HelpPage, TicketPage, TicketsPage } from "../routes/support/SupportPages";
import {
  MerchantPage,
  PaymentResultPage,
  PayPage,
  ReceivePage,
  RequestMoneyPage,
  ScanPage,
  TransferPage,
  TransferResultPage,
} from "../routes/spend/SpendPages";
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
      // The help center is public: people look for answers before they can sign in.
      { path: "help", element: <HelpPage /> },
      { path: "help/:slug", element: <HelpArticlePage /> },
      {
        element: <RequireAuth />,
        children: [
          { path: "onboarding/pin", element: <SetPinPage /> },
          { path: "account", element: <AccountHubPage /> },
          { path: "account/profile", element: <ProfilePage /> },
          { path: "account/recipients", element: <RecipientsPage /> },
          { path: "account/security", element: <SecurityPage /> },
          { path: "settings", element: <SettingsPage /> },
          { path: "support/tickets", element: <TicketsPage /> },
          { path: "support/tickets/:reference", element: <TicketPage /> },
          { path: "wallet", element: <WalletPage /> },
          { path: "history", element: <HistoryPage /> },
          { path: "history/:reference", element: <TransactionDetailPage /> },
          { path: "deposit", element: <DepositPage /> },
          { path: "deposits/:reference", element: <DepositResultPage /> },
          { path: "withdraw", element: <WithdrawPage /> },
          { path: "withdrawals/:reference", element: <WithdrawalResultPage /> },
          { path: "wallet/destinations", element: <DestinationsPage /> },
          { path: "notifications", element: <NotificationsPage /> },
          { path: "transfer", element: <TransferPage /> },
          { path: "transfers/:reference", element: <TransferResultPage /> },
          { path: "pay/:code", element: <PayPage /> },
          { path: "payments/:reference", element: <PaymentResultPage /> },
          { path: "scan", element: <ScanPage /> },
          { path: "receive", element: <ReceivePage /> },
          { path: "request", element: <RequestMoneyPage /> },
          { path: "merchant", element: <MerchantPage /> },
          { path: "kyc", element: <KycPage /> },
          { path: "wallet/limits", element: <LimitsPage /> },
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
