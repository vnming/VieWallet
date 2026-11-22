import { clsx } from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, disables the button and sets aria-busy. */
  loading?: boolean;
  leadingIcon?: ReactNode;
  fullWidth?: boolean;
}

const variants: Record<ButtonVariant, string> = {
  primary: "bg-primary text-primary-fg hover:bg-primary-hover shadow-hairline",
  secondary: "bg-surface text-fg border border-border-strong hover:bg-bg-subtle",
  ghost: "bg-transparent text-fg hover:bg-bg-subtle",
  danger: "bg-danger-700 text-white hover:bg-danger-500",
};

const sizes: Record<ButtonSize, string> = {
  sm: "h-9 px-3 text-sm gap-1.5",
  md: "h-11 px-4 text-base gap-2",
  lg: "h-13 px-6 text-base gap-2",
};

/** Classes of a Button, for links that must look like buttons (no <a><button> nesting). */
export function buttonClassName({
  variant = "primary",
  size = "md",
  fullWidth = false,
}: { variant?: ButtonVariant; size?: ButtonSize; fullWidth?: boolean } = {}): string {
  return clsx(
    "inline-flex select-none items-center justify-center rounded-md font-medium",
    "transition-colors duration-[120ms] ease-(--ease-standard)",
    variants[variant],
    sizes[size],
    fullWidth && "w-full",
  );
}

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  leadingIcon,
  fullWidth = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={clsx(
        "inline-flex select-none items-center justify-center rounded-md font-medium",
        "transition-colors duration-[120ms] ease-(--ease-standard)",
        "disabled:cursor-not-allowed disabled:opacity-55",
        variants[variant],
        sizes[size],
        fullWidth && "w-full",
        className,
      )}
      {...rest}
    >
      {loading ? <Spinner size="sm" /> : leadingIcon}
      <span>{children}</span>
    </button>
  );
}
