/**
 * Money formatting for the UI (ADR-0003).
 *
 * The API sends amounts as strings of integer minor units. They are parsed with BigInt and
 * formatted with Intl.NumberFormat, never converted to floating-point numbers. The frontend
 * only formats money; all arithmetic happens in the backend.
 */

export interface ApiMoney {
  amount: string;
  currency: string;
}

const CURRENCY_EXPONENTS: Record<string, number> = { VND: 0, USD: 2 };
const AMOUNT_PATTERN = /^-?(0|[1-9][0-9]*)$/;

export class InvalidAmountError extends Error {
  constructor(value: string) {
    super(`Invalid money amount: ${JSON.stringify(value)}`);
    this.name = "InvalidAmountError";
  }
}

export function currencyExponent(currency: string): number {
  const exponent = CURRENCY_EXPONENTS[currency];
  if (exponent === undefined) {
    throw new Error(`Unsupported currency: ${currency}`);
  }
  return exponent;
}

export function parseMinorUnits(amount: string): bigint {
  if (!AMOUNT_PATTERN.test(amount)) {
    throw new InvalidAmountError(amount);
  }
  return BigInt(amount);
}

/** Intl accepts a decimal string; build it from integer minor units without using floats. */
function toDecimalString(minor: bigint, exponent: number): string {
  if (exponent === 0) return minor.toString();
  const negative = minor < 0n;
  const digits = (negative ? -minor : minor).toString().padStart(exponent + 1, "0");
  const whole = digits.slice(0, -exponent);
  const fraction = digits.slice(-exponent);
  return `${negative ? "-" : ""}${whole}.${fraction}`;
}

export interface FormatMoneyOptions {
  locale?: string;
  /** Show "+" for positive values (incoming money). */
  signed?: boolean;
}

export function formatMoney(money: ApiMoney, options: FormatMoneyOptions = {}): string {
  const { locale = "vi-VN", signed = false } = options;
  const exponent = currencyExponent(money.currency);
  const minor = parseMinorUnits(money.amount);
  const formatter = new Intl.NumberFormat(locale, {
    style: "currency",
    currency: money.currency,
    minimumFractionDigits: exponent,
    maximumFractionDigits: exponent,
    signDisplay: signed ? "exceptZero" : "auto",
  });
  // Intl.NumberFormat.format accepts numeric strings exactly (ES2023 "Intl.NumberFormat v3").
  return formatter.format(toDecimalString(minor, exponent) as unknown as number);
}
