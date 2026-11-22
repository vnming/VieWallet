import type { WalletInfo } from "@viewallet/api-client";
import { AmountText, Badge, Skeleton, useToast } from "@viewallet/ui";
import { Copy, Eye, EyeOff, Snowflake } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

import { amountLocale } from "./format";

const HIDE_KEY = "vw.hideBalance";

/** Hero balance. The amount can be hidden on shared screens (remembered on this device). */
export function BalanceCard({ wallet }: { wallet: WalletInfo | undefined }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const [hidden, setHidden] = useState(() => localStorage.getItem(HIDE_KEY) === "1");

  const toggle = () => {
    const next = !hidden;
    setHidden(next);
    localStorage.setItem(HIDE_KEY, next ? "1" : "0");
  };

  const copyNumber = async (value: string) => {
    await navigator.clipboard.writeText(value);
    toast.show({ title: t("wallet.copied"), tone: "success" });
  };

  return (
    <section
      aria-labelledby="balance-label"
      className="rounded-xl bg-primary p-5 text-primary-fg shadow-hairline md:p-7"
    >
      <div className="flex items-center justify-between gap-3">
        <h2 id="balance-label" className="text-sm font-medium opacity-90">
          {t("wallet.balanceLabel")}
        </h2>
        <button
          type="button"
          onClick={toggle}
          aria-pressed={hidden}
          aria-label={hidden ? t("wallet.show") : t("wallet.hide")}
          className="grid size-9 place-items-center rounded-full hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white"
        >
          {hidden ? (
            <Eye aria-hidden className="size-5" />
          ) : (
            <EyeOff aria-hidden className="size-5" />
          )}
        </button>
      </div>
      <div className="mt-3 min-h-[3.25rem]" aria-live="polite">
        {!wallet ? (
          <Skeleton className="h-12 w-56 bg-white/20" />
        ) : hidden ? (
          <span className="text-[2.5rem] font-bold leading-none tracking-widest md:text-[3.25rem]">
            <span aria-hidden>••••••</span>
            <span className="sr-only">{t("wallet.hidden")}</span>
          </span>
        ) : (
          <AmountText
            money={wallet.balance}
            size="display"
            locale={amountLocale(i18n.language)}
            className="text-primary-fg"
          />
        )}
      </div>
      {wallet && (
        <div className="mt-5 flex flex-wrap items-center gap-3 text-sm">
          <span className="opacity-90">{t("wallet.number")}</span>
          <span className="tabular font-semibold tracking-wide">
            {wallet.wallet_number.replace(/(\d{3})(\d{3})(\d{4})/, "$1 $2 $3")}
          </span>
          <button
            type="button"
            onClick={() => void copyNumber(wallet.wallet_number)}
            aria-label={t("wallet.copy")}
            className="grid size-8 place-items-center rounded-full hover:bg-white/10 focus-visible:outline-2 focus-visible:outline-white"
          >
            <Copy aria-hidden className="size-4" />
          </button>
          {wallet.status === "FROZEN" && (
            <Badge tone="warning" icon={<Snowflake aria-hidden className="size-3.5" />}>
              {t("wallet.status.FROZEN")}
            </Badge>
          )}
        </div>
      )}
    </section>
  );
}
