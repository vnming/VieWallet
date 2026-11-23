import { AmountText, Button, TextField } from "@viewallet/ui";
import { useTranslation } from "react-i18next";

import { amountLocale } from "../wallet/format";
import { onlyDigits } from "./amount";

const PRESETS = ["100000", "500000", "1000000", "2000000"];

export function PageHeader({ title, description }: { title: string; description: string }) {
  return (
    <header className="flex flex-col gap-1">
      <h1 className="text-2xl font-bold text-fg">{title}</h1>
      <p className="text-fg-muted">{description}</p>
    </header>
  );
}

export function AmountField({
  value,
  onChange,
  hint,
}: {
  value: string;
  onChange: (v: string) => void;
  hint?: string;
}) {
  const { t, i18n } = useTranslation();
  return (
    <div className="flex flex-col gap-3">
      <TextField
        label={t("money.amount")}
        inputMode="numeric"
        hint={hint ?? t("money.amountHint")}
        value={value}
        onChange={(e) => onChange(onlyDigits(e.target.value))}
      />
      <div role="group" aria-label={t("money.presets")} className="flex flex-wrap gap-2">
        {PRESETS.map((preset) => (
          <Button
            key={preset}
            type="button"
            size="sm"
            variant={preset === value ? "primary" : "secondary"}
            onClick={() => onChange(preset)}
          >
            <AmountText
              money={{ amount: preset, currency: "VND" }}
              locale={amountLocale(i18n.language)}
              size="sm"
            />
          </Button>
        ))}
      </div>
    </div>
  );
}
