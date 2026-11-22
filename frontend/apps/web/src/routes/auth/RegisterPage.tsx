import { fieldErrors, useIdentityApi } from "@viewallet/api-client";
import { isLocale } from "@viewallet/shared";
import { Button, InlineAlert, PasswordField, TextField } from "@viewallet/ui";
import { useMutation } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { AuthCard } from "../../features/auth/AuthCard";
import { FormError } from "../../features/auth/FormError";

export function RegisterPage() {
  const { t, i18n } = useTranslation();
  const api = useIdentityApi();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const register = useMutation({
    mutationFn: api.register,
    onSuccess: () => {
      void navigate(`/verify-email?email=${encodeURIComponent(email.trim())}`);
    },
  });
  const errors = fieldErrors(register.error);

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    register.mutate({
      full_name: fullName,
      email: email.trim(),
      password,
      locale: isLocale(i18n.language) ? i18n.language : "vi",
    });
  };

  return (
    <AuthCard
      title={t("auth.register.title")}
      description={t("auth.register.description")}
      footer={
        <>
          {t("auth.register.haveAccount")}{" "}
          <Link to="/login" className="font-medium text-primary underline underline-offset-4">
            {t("auth.login.cta")}
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <InlineAlert tone="warning" title={t("auth.register.simulationTitle")}>
          {t("auth.register.simulationBody")}
        </InlineAlert>
        {register.error && !Object.keys(errors).length ? (
          <FormError error={register.error} />
        ) : null}
        <TextField
          label={t("auth.fields.fullName")}
          autoComplete="name"
          required
          minLength={2}
          maxLength={120}
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          error={errors.full_name}
        />
        <TextField
          label={t("auth.fields.email")}
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={errors.email}
        />
        <PasswordField
          label={t("auth.fields.password")}
          autoComplete="new-password"
          required
          minLength={10}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          hint={t("auth.fields.passwordHint")}
          error={errors.password ? t("auth.fields.passwordWeak") : undefined}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <Button
          type="submit"
          fullWidth
          loading={register.isPending}
          disabled={fullName.trim().length < 2 || !email || password.length < 10}
        >
          {t("auth.register.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
