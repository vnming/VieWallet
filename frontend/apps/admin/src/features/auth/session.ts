const NEXT_STEP_KEY = "vw.admin.nextStep";

/** Remember which MFA step the pending session needs (only the step name, never a secret). */
export function rememberNextStep(step: string | null): void {
  if (step) sessionStorage.setItem(NEXT_STEP_KEY, step);
  else sessionStorage.removeItem(NEXT_STEP_KEY);
}

export function rememberedNextStep(): string | null {
  return sessionStorage.getItem(NEXT_STEP_KEY);
}

/** Only allow same-app relative paths as post-login redirect targets (no open redirect). */
export function safeNext(value: string | null): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) return "/";
  return value;
}
