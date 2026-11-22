import { identityKeys, useIdentityApi } from "@viewallet/api-client";
import {
  Button,
  Card,
  InlineAlert,
  PasswordField,
  PinField,
  Skeleton,
  TextField,
} from "@viewallet/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import QRCode from "qrcode";
import { type SyntheticEvent, type ReactNode, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";

import { rememberNextStep, safeNext } from "../../features/auth/session";
import { FormError } from "../../features/auth/FormError";

function StaffAuthLayout({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto flex w-full max-w-md flex-col gap-6 py-10">
      <div className="flex flex-col gap-2">
        <h1 className="text-[1.75rem] font-bold text-fg">{title}</h1>
        <p className="text-fg-muted">{description}</p>
      </div>
      <Card>{children}</Card>
    </div>
  );
}

export function StaffLoginPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const login = useMutation({
    mutationFn: api.staffLogin,
    onSuccess: ({ next_step }) => {
      rememberNextStep(next_step);
      const next = encodeURIComponent(safeNext(params.get("next")));
      void navigate(
        next_step === "MFA_ENROLLMENT_REQUIRED"
          ? `/mfa/setup?next=${next}`
          : `/mfa/verify?next=${next}`,
        { replace: true },
      );
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    login.mutate({ email, password });
  };
  return (
    <StaffAuthLayout title={t("auth.login.title")} description={t("auth.login.description")}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <FormError error={login.error} />
        <TextField
          label={t("auth.fields.email")}
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <PasswordField
          label={t("auth.fields.password")}
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <Button type="submit" fullWidth loading={login.isPending} disabled={!email || !password}>
          {t("auth.login.submit")}
        </Button>
      </form>
    </StaffAuthLayout>
  );
}

function useFinishLogin() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  return () => {
    rememberNextStep(null);
    // Drop (not just invalidate) the cached "signed out" result, otherwise the guard would
    // read the stale null and bounce back to /login before the refetch lands.
    queryClient.removeQueries({ queryKey: identityKeys.staffMe });
    void navigate(safeNext(params.get("next")), { replace: true });
  };
}

export function MfaSetupPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const finish = useFinishLogin();
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const enrollment = useQuery({
    queryKey: ["admin", "mfa-enrollment"],
    queryFn: async () => {
      const data = await api.mfaEnroll();
      const qr = await QRCode.toDataURL(data.otpauth_uri, { margin: 1, width: 220 });
      return { ...data, qr };
    },
    staleTime: Infinity,
    gcTime: 0, // the secret must not linger in the cache once the page is left
    retry: false,
  });
  const confirm = useMutation({
    mutationFn: api.mfaConfirm,
    onSuccess: (data) => setRecoveryCodes(data.recovery_codes),
  });

  if (recoveryCodes) {
    return (
      <StaffAuthLayout title={t("mfa.recovery.title")} description={t("mfa.recovery.description")}>
        <div className="flex flex-col gap-4">
          <InlineAlert tone="warning" title={t("mfa.recovery.warning")} />
          <ul
            className="grid grid-cols-2 gap-2 font-mono text-sm"
            aria-label={t("mfa.recovery.title")}
          >
            {recoveryCodes.map((c) => (
              <li
                key={c}
                className="rounded-md border border-border bg-bg-subtle px-3 py-2 text-center"
              >
                {c}
              </li>
            ))}
          </ul>
          <Button fullWidth onClick={finish}>
            {t("mfa.recovery.done")}
          </Button>
        </div>
      </StaffAuthLayout>
    );
  }

  return (
    <StaffAuthLayout title={t("mfa.setup.title")} description={t("mfa.setup.description")}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          confirm.mutate(code);
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        <FormError error={enrollment.error ?? confirm.error} />
        {enrollment.isPending ? (
          <Skeleton className="mx-auto size-[220px]" />
        ) : enrollment.data ? (
          <div className="flex flex-col items-center gap-3">
            <img
              src={enrollment.data.qr}
              alt={t("mfa.setup.qrAlt")}
              className="size-[220px] rounded-md bg-white p-2"
            />
            <p className="text-center text-xs text-fg-muted">
              {t("mfa.setup.manual")}{" "}
              <code className="break-all font-mono text-fg">{enrollment.data.secret}</code>
            </p>
          </div>
        ) : null}
        <PinField label={t("mfa.code")} masked={false} value={code} onValueChange={setCode} />
        <Button
          type="submit"
          fullWidth
          loading={confirm.isPending}
          disabled={code.length !== 6 || !enrollment.data}
        >
          {t("mfa.setup.submit")}
        </Button>
      </form>
    </StaffAuthLayout>
  );
}

export function MfaVerifyPage() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const finish = useFinishLogin();
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState("");
  const verify = useMutation({ mutationFn: api.mfaVerify, onSuccess: finish });
  return (
    <StaffAuthLayout title={t("mfa.verify.title")} description={t("mfa.verify.description")}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          verify.mutate(useRecovery ? { recovery_code: recovery } : { code });
        }}
        className="flex flex-col gap-4"
        noValidate
      >
        <FormError error={verify.error} />
        {useRecovery ? (
          <TextField
            label={t("mfa.recoveryCode")}
            autoComplete="off"
            value={recovery}
            onChange={(e) => setRecovery(e.target.value)}
          />
        ) : (
          <PinField label={t("mfa.code")} masked={false} value={code} onValueChange={setCode} />
        )}
        <Button
          type="submit"
          fullWidth
          loading={verify.isPending}
          disabled={useRecovery ? recovery.trim().length < 10 : code.length !== 6}
        >
          {t("mfa.verify.submit")}
        </Button>
        <Button variant="ghost" fullWidth onClick={() => setUseRecovery((v) => !v)}>
          {useRecovery ? t("mfa.verify.useApp") : t("mfa.verify.useRecovery")}
        </Button>
      </form>
    </StaffAuthLayout>
  );
}
