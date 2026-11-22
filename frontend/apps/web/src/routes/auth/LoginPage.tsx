import { identityKeys, isApiError, useIdentityApi } from "@viewallet/api-client";
import { Button, InlineAlert, PasswordField, TextField } from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router";

import { AuthCard } from "../../features/auth/AuthCard";
import { FormError } from "../../features/auth/FormError";
import { safeNext } from "../../features/auth/navigation";

export function LoginPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState(params.get("email") ?? "");
  const [password, setPassword] = useState("");

  const login = useMutation({
    mutationFn: api.login,
    onSuccess: (me) => {
      queryClient.setQueryData(identityKeys.me, me);
      void navigate(me.pin.is_set ? safeNext(params.get("next")) : "/onboarding/pin", {
        replace: true,
      });
    },
  });

  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    login.mutate({ email, password });
  };

  const notVerified = isApiError(login.error, "EMAIL_NOT_VERIFIED");

  return (
    <AuthCard
      title={t("auth.login.title")}
      description={t("auth.login.description")}
      footer={
        <>
          {t("auth.login.noAccount")}{" "}
          <Link to="/register" className="font-medium text-primary underline underline-offset-4">
            {t("auth.register.cta")}
          </Link>
        </>
      }
    >
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        {params.get("verified") === "1" && (
          <InlineAlert tone="success" title={t("auth.verify.done")} />
        )}
        {params.get("reset") === "1" && <InlineAlert tone="success" title={t("auth.reset.done")} />}
        {notVerified ? (
          <InlineAlert tone="warning" title={t("auth.login.notVerified")}>
            <Link
              to={`/verify-email?email=${encodeURIComponent(email)}`}
              className="font-medium underline underline-offset-4"
            >
              {t("auth.login.verifyNow")}
            </Link>
          </InlineAlert>
        ) : (
          <FormError error={login.error} />
        )}
        <TextField
          label={t("auth.fields.email")}
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <PasswordField
          label={t("auth.fields.password")}
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <div className="flex justify-end">
          <Link to="/forgot-password" className="text-sm text-primary underline underline-offset-4">
            {t("auth.login.forgot")}
          </Link>
        </div>
        <Button type="submit" fullWidth loading={login.isPending} disabled={!email || !password}>
          {t("auth.login.submit")}
        </Button>
      </form>
    </AuthCard>
  );
}
