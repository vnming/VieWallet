import { clsx } from "clsx";

export interface SpinnerProps {
  size?: "sm" | "md" | "lg";
  /** Accessible label; omit when the spinner sits inside an element that already has one. */
  label?: string;
  className?: string;
}

const sizes = { sm: "size-4 border-2", md: "size-6 border-2", lg: "size-10 border-3" };

export function Spinner({ size = "md", label, className }: SpinnerProps) {
  return (
    <span
      role={label ? "status" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      className={clsx(
        "inline-block animate-spin rounded-full border-current border-r-transparent",
        sizes[size],
        className,
      )}
    />
  );
}
