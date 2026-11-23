/** Amount input helpers (integer dong, no separators). */

export function onlyDigits(value: string): string {
  return value.replace(/\D/g, "").replace(/^0+/, "").slice(0, 13);
}

/** Deposits and withdrawals move whole thousands of dong. */
export function validAmount(value: string): boolean {
  return /^[1-9]\d*000$/.test(value);
}
