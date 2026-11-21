import { clsx } from "clsx";

export interface SkeletonProps {
  className?: string;
}

/** Decorative placeholder while content loads; the loading container announces the state. */
export function Skeleton({ className }: SkeletonProps) {
  return (
    <span
      aria-hidden
      className={clsx(
        "block rounded-md bg-[linear-gradient(90deg,var(--vw-bg-subtle)_25%,var(--vw-border)_50%,var(--vw-bg-subtle)_75%)]",
        "bg-[length:200%_100%] animate-shimmer",
        className,
      )}
    />
  );
}
