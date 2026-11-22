import { fieldErrors, useIdentityApi } from "@viewallet/api-client";
import { Button, InlineAlert, PasswordField, TextField } from "@viewallet/ui";
import { useMutation } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import { AuthCard } from "../../features/auth/AuthCard";
import { FormError } from "../../features/auth/FormError";

export function ForgotPasswordPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const [email, setEmail] = useState("");
  const forgot = useMutation({ mutationFn: api.forgotPassword });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    forgot.mutate(email.trim());
  };

  return (
    <AuthCard
      title={t("auth.forgot.title")}
      description={t("auth.forgot.description")}
      footer={
        <Link to="/login" className="font-medium text-primary underline underline-offset-4">
          {t("auth.login.cta")}
        </Link>
      }
    >
      {forgot.isSuccess ? (
        // Same message whether or not the address has an account (no enumeration).
        <InlineAlert tone="success" title={t("auth.forgot.sent")}>
          {t("auth.forgot.sentBody")}
        </InlineAlert>
      ) : (
        <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
          <FormError error={forgot.error} />
          <TextField
            label={t("auth.fields.email")}
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Button type="submit" fullWidth loading={forgot.isPending} disabled={!email}>
            {t("auth.forgot.submit")}
          </Button>
        </form>
      )}
    </AuthCard>
  );
}

export function ResetPasswordPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const reset = useMutation({
    mutationFn: api.resetPassword,
    onSuccess: () => void navigate("/login?reset=1", { replace: true }),
  });
  const errors = fieldErrors(reset.error);
  const mismatch = confirm.length > 0 && confirm !== password;

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    if (!mismatch) reset.mutate({ token, new_password: password });
  };

  if (!token) {
    return (
      <AuthCard title={t("auth.reset.title")}>
        <InlineAlert tone="danger" title={t("auth.reset.missingToken")} />
      </AuthCard>
    );
  }

  return (
    <AuthCard title={t("auth.reset.title")} description={t("auth.reset.description")}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {!errors.new_password && <FormError error={reset.error} />}
        <PasswordField
          label={t("auth.fields.newPassword")}
          autoComplete="new-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={t("auth.fields.passwordHint")}
          error={errors.new_password ? t("auth.fields.passwordWeak") : undefined}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <PasswordField
          label={t("auth.fields.confirmPassword")}
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          error={mismatch ? t("auth.fields.passwordMismatch") : undefined}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <Button
          type="submit"
          fullWidth
          loading={reset.isPending}
          disabled={password.length < 10 || mismatch || !confirm}
        >
          {t("auth.reset.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
