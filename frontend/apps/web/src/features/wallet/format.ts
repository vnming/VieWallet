import type { TransactionItem } from "@viewallet/api-client";
import type { TFunction } from "i18next";

export function amountLocale(language: string): string {
  return language === "en" ? "en-US" : "vi-VN";
}

export function uiLocale(language: string): "vi" | "en" {
  return language === "en" ? "en" : "vi";
}

export function formatDateTime(iso: string, language: string): string {
  return new Intl.DateTimeFormat(amountLocale(language), {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Ho_Chi_Minh",
  }).format(new Date(iso));
}

/** Customer-facing title: the stored description, or the type name. */
export function transactionTitle(txn: TransactionItem, t: TFunction): string {
  return txn.description || t(`txn.types.${txn.type}`);
}
