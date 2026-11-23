import { useIdentityApi, useStaffMe, useSystemStatus } from "@viewallet/api-client";
import { isLocale, rememberLocale } from "@viewallet/shared";
import { Badge, Button } from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import {
  BadgeCheck,
  ClipboardCheck,
  LayoutDashboard,
  type LucideIcon,
  Scale,
  ScrollText,
  ShieldAlert,
  SlidersHorizontal,
  WalletCards,
} from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet, useNavigate } from "react-router";

import { APPROVER_PERMISSIONS } from "../features/auth/permissions";

interface NavItem {
  to: string;
  key: string;
  icon: LucideIcon;
  end: boolean;
  /** Shown only when the staff member holds one of these (the API enforces it anyway). */
  permission?: string[];
}

const NAV_ITEMS: NavItem[] = [
  { to: "/", key: "nav.overview", icon: LayoutDashboard, end: true },
  {
    to: "/wallets",
    key: "nav.wallets",
    icon: WalletCards,
    end: false,
    permission: ["user:read:any"],
  },
  {
    to: "/adjustments",
    key: "nav.adjustments",
    icon: SlidersHorizontal,
    end: false,
    permission: ["adjustment:propose:any"],
  },
  {
    to: "/approvals",
    key: "nav.approvals",
    icon: ClipboardCheck,
    end: false,
    permission: APPROVER_PERMISSIONS,
  },
  {
    to: "/reconciliation",
    key: "nav.reconciliation",
    icon: Scale,
    end: false,
    permission: ["reconciliation:manage:any"],
  },
  { to: "/kyc", key: "nav.kyc", icon: BadgeCheck, end: false, permission: ["kyc:review:any"] },
  {
    to: "/risk",
    key: "nav.risk",
    icon: ShieldAlert,
    end: false,
    permission: ["risk_hold:decide:any"],
  },
  { to: "/audit", key: "nav.audit", icon: ScrollText, end: false, permission: ["audit:read:any"] },
];

function StaffMenu() {
  const { t } = useTranslation();
  const me = useStaffMe();
  const api = useIdentityApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const logout = useMutation({
    mutationFn: api.staffLogout,
    onSettled: () => {
      queryClient.clear();
      void navigate("/login", { replace: true });
    },
  });
  if (!me.data) return null;
  return (
    <div className="flex items-center gap-3">
      <span className="hidden text-right text-sm leading-tight sm:block">
        <span className="block font-medium text-fg">{me.data.full_name}</span>
        <span className="block text-xs text-fg-muted">{me.data.roles.join(", ")}</span>
      </span>
      <Button
        size="sm"
        variant="secondary"
        loading={logout.isPending}
        onClick={() => logout.mutate()}
      >
        {t("auth.logout")}
      </Button>
    </div>
  );
}

function EnvironmentBadge() {
  const { t } = useTranslation();
  const { data } = useSystemStatus();
  if (!data) return null;
  const production = data.environment === "production-like";
  return (
    <Badge tone={production ? "danger" : "warning"}>
      {t("app.environment")}: {data.environment.toUpperCase()}
    </Badge>
  );
}

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="sr-only">{t("language.label")}</span>
      <select
        value={i18n.language}
        onChange={(event) => {
          const next = event.target.value;
          if (isLocale(next)) {
            rememberLocale(next);
            void i18n.changeLanguage(next);
          }
        }}
        className="h-9 rounded-md border border-border bg-surface px-2 text-sm text-fg"
      >
        <option value="vi">{t("language.vi")}</option>
        <option value="en">{t("language.en")}</option>
      </select>
    </label>
  );
}

/** Staff shell: collapsible-ready left sidebar, top bar with environment badge, wide content. */
export function AdminShell() {
  const { t, i18n } = useTranslation();
  const me = useStaffMe();
  const permissions = me.data?.permissions ?? [];
  const navItems = NAV_ITEMS.filter(
    (item) => !item.permission || item.permission.some((p) => permissions.includes(p)),
  );

  useEffect(() => {
    document.documentElement.lang = i18n.language;
  }, [i18n.language]);

  return (
    <div className="grid min-h-dvh grid-cols-1 bg-bg md:grid-cols-[16rem_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
      >
        {t("app.skipToContent")}
      </a>
      <aside className="border-b border-border bg-ink-950 px-4 py-4 text-ink-100 md:border-b-0 md:border-r md:py-6">
        <div className="mb-6 flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="size-8 rounded-md" />
          <span className="flex flex-col leading-tight">
            <span className="text-base font-bold text-white">{t("app.name")}</span>
            <span className="text-xs text-ink-300">{t("app.tagline")}</span>
          </span>
        </div>
        <nav aria-label={t("nav.label")} className="flex gap-1 md:flex-col">
          {navItems.map(({ to, key, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-jade-900 text-white"
                    : "text-ink-300 hover:bg-ink-900 hover:text-white",
                )
              }
            >
              <Icon aria-hidden className="size-4.5" />
              {t(key)}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-col">
        <header className="flex h-14 items-center justify-between gap-4 border-b border-border bg-surface px-4 md:px-8">
          <EnvironmentBadge />
          <div className="flex items-center gap-3">
            <LanguageSwitch />
            <StaffMenu />
          </div>
        </header>
        <main id="main" tabIndex={-1} className="flex-1 px-4 py-6 md:px-8">
          <div className="mx-auto w-full max-w-[1440px]">
            <Outlet />
          </div>
        </main>
      </div>
    </div>
  );
}
