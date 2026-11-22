/** Only allow same-app relative paths as post-login redirect targets (no open redirect). */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}
