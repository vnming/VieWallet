import { ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { SystemStatusCard } from "../features/system/SystemStatusCard";

export function HomePage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-6">
      <section className="flex flex-col gap-3">
        <h1 className="text-[1.75rem] font-bold leading-tight text-fg md:text-[2rem]">
          {t("home.title")}
        </h1>
        <p className="max-w-2xl text-fg-muted">{t("home.intro")}</p>
        <p className="inline-flex w-fit items-center gap-2 rounded-full bg-saffron-100 px-3 py-1 text-xs font-medium text-saffron-700 md:hidden">
          <ShieldCheck aria-hidden className="size-4" />
          {t("app.simulationNotice")}
        </p>
      </section>
      <SystemStatusCard />
    </div>
  );
}
