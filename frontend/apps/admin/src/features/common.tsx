/** Small building blocks shared by the Phase 8 admin pages. */
import { intlLocale } from "@viewallet/shared";
import { AmountText } from "@viewallet/ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <header className="flex flex-wrap items-end justify-between gap-4">
      <div className="flex flex-col gap-2">
        <h1 className="text-[1.75rem] font-bold text-fg">{title}</h1>
        {description && <p className="max-w-3xl text-fg-muted">{description}</p>}
      </div>
      {actions}
    </header>
  );
}

export function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-fg-muted">{label}</dt>
      <dd className="text-sm font-medium text-fg">{children}</dd>
    </div>
  );
}

export function SelectField<T extends string>({
  label,
  value,
  options,
  onChange,
  labelFor,
  className,
}: {
  label: string;
  value: T;
  options: readonly T[];
  onChange: (value: T) => void;
  labelFor: (value: T) => string;
  className?: string;
}) {
  return (
    <label className={`flex flex-col gap-1.5 text-sm font-medium text-fg ${className ?? ""}`}>
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value as T)}
        className="h-11 rounded-md border border-border-strong bg-surface px-3 text-sm"
      >
        {options.map((o) => (
          <option key={o} value={o}>
            {labelFor(o)}
          </option>
        ))}
      </select>
    </label>
  );
}

export function Money({ amount, currency = "VND" }: { amount: string; currency?: string }) {
  const { i18n } = useTranslation();
  return <AmountText money={{ amount, currency }} size="sm" locale={intlLocale(i18n.language)} />;
}

/** Plain table with a visually hidden caption (screen readers announce what it lists). */
export function DataTable({
  caption,
  headers,
  children,
}: {
  caption: string;
  headers: string[];
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="border-b border-border text-xs uppercase tracking-wide text-fg-muted">
          <tr>
            {headers.map((h) => (
              <th key={h} scope="col" className="py-2 pr-3">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-border">{children}</tbody>
      </table>
    </div>
  );
}
