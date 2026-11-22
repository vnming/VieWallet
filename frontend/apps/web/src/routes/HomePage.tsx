import { useMe } from "@viewallet/api-client";
import { buttonClassName, InlineAlert } from "@viewallet/ui";
import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { SystemStatusCard } from "../features/system/SystemStatusCard";
import { WalletOverview } from "./wallet/WalletPages";

export function HomePage() {
  const { t } = useTranslation();
  const me = useMe();
  const user = me.data;

  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-[1.75rem] font-bold leading-tight text-fg md:text-[2rem]">
          {user
            ? t("home.greeting", { name: user.display_name ?? user.full_name })
            : t("home.title")}
        </h1>
        <p className="max-w-2xl text-fg-muted">
          {user ? t("home.signedInIntro") : t("home.intro")}
        </p>
        <p className="inline-flex w-fit items-center gap-2 rounded-full bg-saffron-100 px-3 py-1 text-xs font-medium text-saffron-700 md:hidden">
          <ShieldCheck aria-hidden className="size-4" />
          {t("app.simulationNotice")}
        </p>
        {me.isSuccess && !user && (
          <div className="flex flex-wrap gap-3">
            <Link to="/register" className={buttonClassName()}>
              {t("auth.register.cta")}
            </Link>
            <Link to="/login" className={buttonClassName({ variant: "secondary" })}>
              {t("auth.login.cta")}
            </Link>
          </div>
        )}
        {user && !user.pin.is_set && (
          <InlineAlert tone="warning" title={t("pin.missingTitle")}>
            <Link to="/onboarding/pin" className="font-medium underline underline-offset-4">
              {t("pin.setup.submit")}
            </Link>
          </InlineAlert>
        )}
      </section>
      {user ? <WalletOverview /> : <SystemStatusCard />}
    </div>
  );
}
