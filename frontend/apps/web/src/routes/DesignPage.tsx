/**
 * Living style guide (development builds link it in the navigation).
 * Shows every Phase 1 design-system component in its main states, with sample data only.
 */
import {
  AmountText,
  Badge,
  Button,
  Card,
  Dialog,
  EmptyState,
  ErrorState,
  Skeleton,
  StatusBadge,
  TextField,
  TRANSACTION_STATUSES,
  useToast,
} from "@viewallet/ui";
import { Plus } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

const SWATCHES = [
  ["bg-bg", "bg"],
  ["bg-surface", "surface"],
  ["bg-primary", "primary"],
  ["bg-primary-soft", "primary-soft"],
  ["bg-accent", "accent"],
  ["bg-success-500", "success"],
  ["bg-warning-500", "warning"],
  ["bg-danger-500", "danger"],
  ["bg-info-500", "info"],
] as const;

export function DesignPage() {
  const { i18n } = useTranslation();
  const locale = i18n.language === "en" ? "en" : "vi";
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [theme, setTheme] = useState<"system" | "light" | "dark">("system");

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[1.75rem] font-bold text-fg">Design system — Jade Ledger</h1>
          <p className="text-fg-muted">Phase 1 foundation components. Sample data only.</p>
        </div>
        <div role="group" aria-label="Theme" className="flex gap-2">
          {(["system", "light", "dark"] as const).map((value) => (
            <Button
              key={value}
              size="sm"
              variant={theme === value ? "primary" : "secondary"}
              aria-pressed={theme === value}
              onClick={() => setTheme(value)}
            >
              {value}
            </Button>
          ))}
        </div>
      </header>

      <Card title="Colour tokens">
        <ul className="grid grid-cols-3 gap-3 sm:grid-cols-5 lg:grid-cols-9">
          {SWATCHES.map(([cls, name]) => (
            <li key={name} className="flex flex-col gap-1.5 text-xs text-fg-muted">
              <span className={`h-12 rounded-md border border-border ${cls}`} />
              {name}
            </li>
          ))}
        </ul>
      </Card>

      <Card title="Typography & amounts">
        <div className="flex flex-col gap-3">
          <AmountText money={{ amount: "12500000", currency: "VND" }} size="display" />
          <div className="flex flex-wrap gap-6">
            <AmountText money={{ amount: "250000", currency: "VND" }} direction="IN" size="lg" />
            <AmountText money={{ amount: "1100", currency: "VND" }} direction="OUT" size="lg" />
            <span className="font-mono text-sm text-fg-muted">VW261121K7Q2M9XD4P</span>
          </div>
        </div>
      </Card>

      <Card title="Buttons">
        <div className="flex flex-wrap gap-3">
          <Button>Primary</Button>
          <Button variant="secondary">Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">Danger</Button>
          <Button leadingIcon={<Plus aria-hidden className="size-4" />}>With icon</Button>
          <Button loading>Loading</Button>
          <Button disabled>Disabled</Button>
        </div>
      </Card>

      <Card title="Inputs">
        <div className="grid gap-4 md:grid-cols-2">
          <TextField label="Email" type="email" placeholder="ban@example.com" hint="Hint text" />
          <TextField label="Amount" inputMode="numeric" defaultValue="12a" error="Digits only" />
        </div>
      </Card>

      <Card title="Badges & transaction statuses">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {(["neutral", "primary", "success", "warning", "danger", "info"] as const).map(
              (tone) => (
                <Badge key={tone} tone={tone}>
                  {tone}
                </Badge>
              ),
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            {TRANSACTION_STATUSES.map((status) => (
              <StatusBadge key={status} status={status} locale={locale} />
            ))}
          </div>
        </div>
      </Card>

      <div className="grid gap-6 md:grid-cols-2">
        <Card title="Loading">
          <div role="status" aria-label="Loading example" className="flex flex-col gap-3">
            <Skeleton className="h-6 w-40" />
            <Skeleton className="h-4 w-full" />
            <Skeleton className="h-4 w-2/3" />
          </div>
        </Card>
        <Card title="Empty">
          <EmptyState title="No transactions yet" description="Example empty state." />
        </Card>
        <Card title="Error">
          <ErrorState
            title="Could not load data"
            description="Example error state."
            correlationId="0193f0c28f5e7c3a"
            onRetry={() => toast.show({ title: "Retry clicked", tone: "info" })}
          />
        </Card>
        <Card title="Feedback">
          <div className="flex flex-wrap gap-3">
            <Button
              variant="secondary"
              onClick={() =>
                toast.show({ title: "Saved", description: "Example toast.", tone: "success" })
              }
            >
              Success toast
            </Button>
            <Button
              variant="secondary"
              onClick={() => toast.show({ title: "Something failed", tone: "danger" })}
            >
              Error toast
            </Button>
            <Button variant="secondary" onClick={() => setOpen(true)}>
              Open dialog
            </Button>
          </div>
        </Card>
      </div>

      <Dialog
        open={open}
        onOpenChange={setOpen}
        title="Example dialog"
        description="Focus is trapped; Escape closes it."
        footer={
          <>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={() => setOpen(false)}>Confirm</Button>
          </>
        }
      />
    </div>
  );
}
