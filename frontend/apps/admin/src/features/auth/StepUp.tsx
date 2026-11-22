import { ApiError, useIdentityApi } from "@viewallet/api-client";
import { Button, Dialog, PinField } from "@viewallet/ui";
import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { FormError } from "./FormError";

export function needsStepUp(error: unknown): boolean {
  return error instanceof ApiError && error.code === "MFA_STEP_UP_REQUIRED";
}

/**
 * Sensitive actions answer MFA_STEP_UP_REQUIRED when the last TOTP check is older than 15
 * minutes. ``guard(retry)`` opens the TOTP dialog and re-runs the action after a fresh code.
 */
export function useStepUp() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const [open, setOpen] = useState(false);
  const [code, setCode] = useState("");
  const retryRef = useRef<(() => void) | null>(null);
  const stepUp = useMutation({
    mutationFn: api.stepUp,
    onSuccess: () => {
      setOpen(false);
      setCode("");
      retryRef.current?.();
    },
  });

  const guard = (retry: () => void) => {
    retryRef.current = retry;
    setOpen(true);
  };

  const dialog = (
    <Dialog
      open={open}
      onOpenChange={setOpen}
      title={t("mfa.stepUp.title")}
      description={t("mfa.stepUp.description")}
      closeLabel={t("common.close")}
      footer={
        <Button
          loading={stepUp.isPending}
          disabled={code.length !== 6}
          onClick={() => stepUp.mutate(code)}
        >
          {t("mfa.stepUp.submit")}
        </Button>
      }
    >
      <div className="flex flex-col gap-3">
        <FormError error={stepUp.error} />
        <PinField label={t("mfa.code")} masked={false} value={code} onValueChange={setCode} />
      </div>
    </Dialog>
  );
  return { guard, dialog };
}
