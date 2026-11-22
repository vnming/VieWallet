import { identityKeys, useIdentityApi } from "@viewallet/api-client";
import { Button, InlineAlert, PasswordField, PinField } from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { AuthCard } from "../../features/auth/AuthCard";
import { FormError } from "../../features/auth/FormError";

/** First-run step: every money-out action needs a transaction PIN (FR-PIN-01). */
export function SetPinPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [pin, setPin] = useState("");
  const [confirm, setConfirm] = useState("");
  const mismatch = confirm.length === 6 && confirm !== pin;

  const setPinMutation = useMutation({
    mutationFn: api.setPin,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: identityKeys.me });
      void navigate("/", { replace: true });
    },
  });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!mismatch) setPinMutation.mutate({ password, pin });
  };

  return (
    <AuthCard title={t("pin.setup.title")} description={t("pin.setup.description")}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <InlineAlert tone="info" title={t("pin.rulesTitle")}>
          {t("pin.rules")}
        </InlineAlert>
        <FormError error={setPinMutation.error} />
        <PinField label={t("pin.new")} value={pin} onValueChange={setPin} />
        <PinField
          label={t("pin.confirm")}
          value={confirm}
          onValueChange={setConfirm}
          error={mismatch ? t("pin.mismatch") : undefined}
        />
        <PasswordField
          label={t("auth.fields.password")}
          hint={t("pin.passwordHint")}
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <Button
          type="submit"
          fullWidth
          loading={setPinMutation.isPending}
          disabled={pin.length !== 6 || confirm !== pin || !password}
        >
          {t("pin.setup.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
