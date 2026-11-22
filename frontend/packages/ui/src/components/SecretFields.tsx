import { clsx } from "clsx";
import { Eye, EyeOff } from "lucide-react";
import { type InputHTMLAttributes, type ReactNode, useId, useState } from "react";

interface BaseProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "id" | "type"> {
  label: string;
  hint?: ReactNode;
  error?: string | undefined;
  id?: string;
}

function describedBy(id: string, hint: ReactNode, error: string | undefined) {
  return (
    [hint ? `${id}-hint` : null, error ? `${id}-error` : null].filter(Boolean).join(" ") ||
    undefined
  );
}

function FieldMessages({
  id,
  hint,
  error,
}: {
  id: string;
  hint: ReactNode;
  error?: string | undefined;
}) {
  return (
    <>
      {hint && (
        <p id={`${id}-hint`} className="text-sm text-fg-muted">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-error`} className="text-sm text-danger-700 dark:text-danger-500">
          {error}
        </p>
      )}
    </>
  );
}

export interface PasswordFieldProps extends BaseProps {
  showLabel?: string;
  hideLabel?: string;
}

/** Password input with a show/hide toggle (the value never leaves the input). */
export function PasswordField({
  label,
  hint,
  error,
  id,
  className,
  showLabel = "Hiện mật khẩu",
  hideLabel = "Ẩn mật khẩu",
  ...rest
}: PasswordFieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  const [visible, setVisible] = useState(false);
  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      <label htmlFor={inputId} className="text-sm font-medium text-fg">
        {label}
      </label>
      <div className="relative">
        <input
          id={inputId}
          type={visible ? "text" : "password"}
          aria-invalid={error ? true : undefined}
          aria-describedby={describedBy(inputId, hint, error)}
          className={clsx(
            "h-11 w-full rounded-md border bg-surface pl-3 pr-11 text-base text-fg",
            error ? "border-danger-500" : "border-border-strong focus:border-primary",
          )}
          {...rest}
        />
        <button
          type="button"
          onClick={() => setVisible((v) => !v)}
          aria-label={visible ? hideLabel : showLabel}
          aria-pressed={visible}
          className="absolute inset-y-0 right-0 grid w-11 place-items-center text-fg-muted hover:text-fg"
        >
          {visible ? (
            <EyeOff aria-hidden className="size-4" />
          ) : (
            <Eye aria-hidden className="size-4" />
          )}
        </button>
      </div>
      <FieldMessages id={inputId} hint={hint} error={error} />
    </div>
  );
}

export interface PinFieldProps extends Omit<BaseProps, "value" | "onChange" | "maxLength"> {
  value: string;
  onValueChange: (value: string) => void;
  /** Masked by default; verification codes (not secrets at rest) can be shown. */
  masked?: boolean;
}

/** Six-digit numeric input for transaction PINs and e-mail/TOTP codes. */
export function PinField({
  label,
  hint,
  error,
  id,
  className,
  value,
  onValueChange,
  masked = true,
  ...rest
}: PinFieldProps) {
  const generated = useId();
  const inputId = id ?? generated;
  return (
    <div className={clsx("flex flex-col gap-1.5", className)}>
      <label htmlFor={inputId} className="text-sm font-medium text-fg">
        {label}
      </label>
      <input
        id={inputId}
        type={masked ? "password" : "text"}
        inputMode="numeric"
        autoComplete={masked ? "off" : "one-time-code"}
        pattern="[0-9]{6}"
        maxLength={6}
        value={value}
        onChange={(event) => onValueChange(event.target.value.replace(/\D/g, "").slice(0, 6))}
        aria-invalid={error ? true : undefined}
        aria-describedby={describedBy(inputId, hint, error)}
        className={clsx(
          "tabular h-12 w-full max-w-56 rounded-md border bg-surface px-4 text-center font-mono text-xl tracking-[0.5em] text-fg",
          error ? "border-danger-500" : "border-border-strong focus:border-primary",
        )}
        {...rest}
      />
      <FieldMessages id={inputId} hint={hint} error={error} />
    </div>
  );
}
