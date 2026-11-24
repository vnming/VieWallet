import type { TransactionItem } from "@viewallet/api-client";
import { TransactionRow } from "@viewallet/ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { usePrivacyMode } from "../preferences/privacy";
import { formatDateTime, transactionTitle, uiLocale } from "./format";

export function TransactionList({ items }: { items: TransactionItem[] }) {
  const { t, i18n } = useTranslation();
  const privacy = usePrivacyMode();
  return (
    <ul className="divide-y divide-border">
      {items.map((txn) => (
        <li key={txn.id}>
          <Link
            to={`/history/${txn.reference}`}
            className="-mx-2 block rounded-md px-2 hover:bg-bg-subtle focus-visible:outline-2 focus-visible:outline-primary"
          >
            <TransactionRow
              title={transactionTitle(txn, t)}
              subtitle={formatDateTime(txn.created_at, i18n.language)}
              money={txn.total}
              direction={txn.direction === "IN" ? "IN" : "OUT"}
              status={txn.status}
              locale={uiLocale(i18n.language)}
              {...(privacy ? { maskedLabel: t("settings.amountHidden") } : {})}
            />
          </Link>
        </li>
      ))}
    </ul>
  );
}
