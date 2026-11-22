import type { ReactNode } from "react";

/** Centered card used by every sign-in / sign-up step. */
export function AuthCard({
  title,
  description,
  children,
  footer,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-6 py-4 md:py-10">
      <div className="flex flex-col gap-2">
        <h1 className="text-[1.75rem] font-bold leading-tight text-fg">{title}</h1>
        {description && <p className="text-fg-muted">{description}</p>}
      </div>
      <div className="rounded-xl border border-border bg-surface p-5 shadow-hairline md:p-6">
        {children}
      </div>
      {footer && <div className="text-center text-sm text-fg-muted">{footer}</div>}
    </div>
  );
}
