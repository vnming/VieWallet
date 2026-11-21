import { type ApiMoney, formatMoney, parseMinorUnits } from "@viewallet/shared";
import { clsx } from "clsx";

export interface AmountTextProps {
  money: ApiMoney;
  /** IN = money received (+, green), OUT = money spent (−), NEUTRAL = balances/totals. */
  direction?: "IN" | "OUT" | "NEUTRAL";
  locale?: string;
  size?: "sm" | "md" | "lg" | "display";
  className?: string;
}

const sizes = {
  sm: "text-sm",
  md: "text-base",
  lg: "text-xl font-semibold",
  display: "text-[2.5rem] leading-none font-bold md:text-[3.25rem] tracking-tight",
};

/** Formats an API amount exactly (BigInt-based) with tabular digits and a direction sign. */
export function AmountText({
  money,
  direction = "NEUTRAL",
  locale = "vi-VN",
  size = "md",
  className,
}: AmountTextProps) {
  const minor = parseMinorUnits(money.amount);
  const magnitude = minor < 0n ? (-minor).toString() : minor.toString();
  let text: string;
  if (direction === "NEUTRAL") {
    text = formatMoney(money, { locale });
  } else {
    const formatted = formatMoney({ amount: magnitude, currency: money.currency }, { locale });
    text = `${direction === "IN" ? "+" : "−"}${formatted}`;
  }
  return (
    <span
      className={clsx(
        "tabular whitespace-nowrap",
        direction === "IN" && "text-amount-in",
        sizes[size],
        className,
      )}
    >
      {text}
    </span>
  );
}
