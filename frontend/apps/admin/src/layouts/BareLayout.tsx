import type { ReactNode } from "react";

import { LanguageSwitch } from "./AdminShell";

/** Sign-in steps render without the navigation shell. */
export function BareLayout({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-dvh bg-bg px-4">
      <div className="mx-auto flex max-w-md justify-end pt-4">
        <LanguageSwitch />
      </div>
      <main id="main">{children}</main>
    </div>
  );
}
