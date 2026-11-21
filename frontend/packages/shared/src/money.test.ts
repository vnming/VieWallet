import { describe, expect, it } from "vitest";

// Intl inserts NBSP (U+00A0) and narrow NBSP (U+202F); normalize them for assertions.
const INTL_SPACES = new RegExp(`[${String.fromCharCode(0xa0, 0x202f)}]`, "g");

import { InvalidAmountError, currencyExponent, formatMoney, parseMinorUnits } from "./money";

// Intl uses non-breaking spaces; normalize for readable assertions.
const plain = (s: string) => s.replace(INTL_SPACES, " ");

describe("formatMoney", () => {
  it("formats VND with Vietnamese grouping and the dong sign after the amount", () => {
    expect(plain(formatMoney({ amount: "1250000", currency: "VND" }))).toBe("1.250.000 ₫");
  });

  it("formats zero and negative amounts", () => {
    expect(plain(formatMoney({ amount: "0", currency: "VND" }))).toBe("0 ₫");
    expect(plain(formatMoney({ amount: "-50000", currency: "VND" }))).toBe("-50.000 ₫");
  });

  it("shows a plus sign for incoming money when signed", () => {
    expect(plain(formatMoney({ amount: "200000", currency: "VND" }, { signed: true }))).toBe(
      "+200.000 ₫",
    );
  });

  it("keeps exact precision beyond Number.MAX_SAFE_INTEGER", () => {
    const amount = "9007199254740993"; // 2^53 + 1, not representable as a double
    expect(plain(formatMoney({ amount, currency: "VND" }, { locale: "en-US" }))).toBe(
      "₫9,007,199,254,740,993",
    );
  });

  it("formats currencies with minor units without floats", () => {
    expect(formatMoney({ amount: "123456", currency: "USD" }, { locale: "en-US" })).toBe(
      "$1,234.56",
    );
    expect(formatMoney({ amount: "5", currency: "USD" }, { locale: "en-US" })).toBe("$0.05");
    expect(formatMoney({ amount: "-5", currency: "USD" }, { locale: "en-US" })).toBe("-$0.05");
  });

  it("rejects malformed amounts", () => {
    for (const bad of ["1.5", "01", "", "abc", " 1", "1e3"]) {
      expect(() => formatMoney({ amount: bad, currency: "VND" })).toThrow(InvalidAmountError);
    }
  });

  it("rejects unknown currencies", () => {
    expect(() => currencyExponent("XYZ")).toThrow("Unsupported currency");
  });
});

describe("parseMinorUnits", () => {
  it("parses to bigint", () => {
    expect(parseMinorUnits("1000")).toBe(1000n);
    expect(parseMinorUnits("-1")).toBe(-1n);
  });
});
