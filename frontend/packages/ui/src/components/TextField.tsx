import { clsx } from "clsx";
import { type InputHTMLAttributes, type ReactNode, useId } from "react";

export interface TextFieldProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id"> {
  label: string;
  hint?: ReactNode;
  error?: string | undefined;
  id?: string;
}

/** Labelled input; the hint and error are linked with aria-describedby. */
export function TextField({ label, hint, error, id, className, ...rest }: TextFieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const hintId = hint ? `${inputId}-hint` : undefined;
  const errorId = error ? `${inputId}-error` : undefined;
  const describedBy = [hintId, errorId].filter(Boolean).join(" ") || undefined;

  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      <label htmlFor={inputId} className="text-sm font-medium text-fg">
        {label}
      </label>
      <input
        id={inputId}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy}
        className={clsx(
          "h-11 rounded-md border bg-surface px-3 text-base text-fg",
          "placeholder:text-fg-muted transition-colors",
          error ? "border-danger-500" : "border-border-strong focus:border-primary",
        )}
        {...rest}
      />
      {hint && (
        <p id={hintId} className="text-sm text-fg-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={errorId} className="text-sm text-danger-700 dark:text-danger-500">
          {error}
        </p>
      )}
    </div>
  );
}
