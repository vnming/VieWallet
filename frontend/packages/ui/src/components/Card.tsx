import { clsx } from "clsx";
import type { HTMLAttributes, ReactNode } from "react";

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, "title"> {
  as?: "section" | "article" | "div";
  title?: ReactNode;
  actions?: ReactNode;
}

export function Card({
  as: Tag = "section",
  title,
  actions,
  className,
  children,
  ...rest
}: CardProps) {
  return (
    <Tag
      className={clsx(
        "rounded-xl border border-border bg-surface p-5 shadow-hairline md:p-6",
        className,
      )}
      {...rest}
    >
      {(title || actions) && (
        <header className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="text-lg font-semibold text-fg">{title}</h2>}
          {actions}
        </header>
      )}
      {children}
    </Tag>
  );
}
