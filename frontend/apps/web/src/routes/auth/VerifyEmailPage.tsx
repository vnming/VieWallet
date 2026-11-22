import { useIdentityApi } from "@viewallet/api-client";
import { Button, InlineAlert, PinField, TextField } from "@viewallet/ui";
import { useMutation } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { AuthCard } from "../../features/auth/AuthCard";
import { FormError } from "../../features/auth/FormError";

export function VerifyEmailPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [code, setCode] = useState("");

  const verify = useMutation({
    mutationFn: api.verifyEmail,
    onSuccess: () =>
      void navigate(`/login?verified=1&email=${encodeURIComponent(email)}`, { replace: true }),
  });
  const resend = useMutation({ mutationFn: api.resendVerification });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    verify.mutate({ email, code });
  };

  return (
    <AuthCard title={t("auth.verify.title")} description={t("auth.verify.description")}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <FormError error={verify.error ?? resend.error} />
        {resend.isSuccess && <InlineAlert tone="success" title={t("auth.verify.resent")} />}
        <TextField
          label={t("auth.fields.email")}
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <PinField
          label={t("auth.verify.code")}
          masked={false}
          value={code}
          onValueChange={setCode}
          hint={t("auth.verify.codeHint")}
        />
        <Button
          type="submit"
          fullWidth
          loading={verify.isPending}
          disabled={!email || code.length !== 6}
        >
          {t("auth.verify.submit")}
        </Button>
        <Button
          variant="ghost"
          fullWidth
          loading={resend.isPending}
          disabled={!email}
          onClick={() => resend.mutate(email)}
        >
          {t("auth.verify.resend")}
        </Button>
      </form>
    </AuthCard>
  );
}
