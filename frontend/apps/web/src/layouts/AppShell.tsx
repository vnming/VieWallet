import { isLocale, rememberLocale } from "@viewallet/shared";
import { clsx } from "clsx";
import { Home, Palette } from "lucide-react";
import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { NavLink, Outlet } from "react-router";

const NAV_ITEMS = [
  { to: "/", key: "nav.home", icon: Home, end: true },
  // The living style guide is only linked in development builds.
  ...(import.meta.env.DEV ? [{ to: "/design", key: "nav.design", icon: Palette, end: false }] : []),
] as const;

export function LanguageSwitch() {
  const { t, i18n } = useTranslation();
  return (
    <label className="flex items-center gap-2 text-sm text-fg-muted">
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

/** Customer shell: top bar + bottom tab bar on mobile, side navigation from lg breakpoint. */
export function AppShell() {
  const { t, i18n } = useTranslation();

  useEffect(() => {
    document.documentElement.lang = i18n.language;
  }, [i18n.language]);

  return (
    <div className="min-h-dvh bg-bg lg:grid lg:grid-cols-[15rem_1fr]">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-surface focus:px-3 focus:py-2"
      >
        {t("app.skipToContent")}
      </a>

      <aside className="hidden border-r border-border bg-surface lg:flex lg:flex-col lg:gap-8 lg:px-5 lg:py-6">
        <Brand />
        <nav aria-label={t("nav.label")} className="flex flex-col gap-1">
          {NAV_ITEMS.map(({ to, key, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  "relative flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary-soft text-primary before:absolute before:inset-y-2 before:-left-5 before:w-0.5 before:rounded-full before:bg-primary"
                    : "text-fg-muted hover:bg-bg-subtle hover:text-fg",
                )
              }
            >
              <Icon aria-hidden className="size-5" />
              {t(key)}
            </NavLink>
          ))}
        </nav>
      </aside>

      <div className="flex min-h-dvh flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-border bg-bg/90 px-4 backdrop-blur md:px-8">
          <div className="lg:hidden">
            <Brand />
          </div>
          <p className="hidden text-xs text-fg-muted md:block">{t("app.simulationNotice")}</p>
          <LanguageSwitch />
        </header>

        <main id="main" tabIndex={-1} className="flex-1 px-4 pb-24 pt-6 md:px-8 lg:pb-10">
          <div className="mx-auto w-full max-w-[1120px]">
            <Outlet />
          </div>
        </main>

        <nav
          aria-label={t("nav.label")}
          className="fixed inset-x-0 bottom-0 z-30 grid auto-cols-fr grid-flow-col border-t border-border bg-surface pb-[env(safe-area-inset-bottom)] lg:hidden"
        >
          {NAV_ITEMS.map(({ to, key, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) =>
                clsx(
                  "flex min-h-14 flex-col items-center justify-center gap-0.5 text-xs font-medium",
                  isActive ? "text-primary" : "text-fg-muted",
                )
              }
            >
              <Icon aria-hidden className="size-5" />
              {t(key)}
            </NavLink>
          ))}
        </nav>
      </div>
    </div>
  );
}

function Brand() {
  const { t } = useTranslation();
  return (
    <a href="/" className="flex items-center gap-2.5">
      <img src="/favicon.svg" alt="" className="size-8 rounded-md" />
      <span className="flex flex-col leading-tight">
        <span className="text-base font-bold text-fg">{t("app.name")}</span>
        <span className="text-xs text-fg-muted">{t("app.tagline")}</span>
      </span>
    </a>
  );
}
