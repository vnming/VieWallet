import { identityKeys, useIdentityApi, useMe } from "@viewallet/api-client";
import { intlLocale } from "@viewallet/shared";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  PasswordField,
  PinField,
  Skeleton,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";

import { FormError } from "../../features/auth/FormError";

function useDateTime() {
  const { i18n } = useTranslation();
  const formatter = new Intl.DateTimeFormat(intlLocale(i18n.language), {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Ho_Chi_Minh",
  });
  return (iso: string) => formatter.format(new Date(iso));
}

function ChangePasswordCard() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const mutation = useMutation({
    mutationFn: api.changePassword,
    onSuccess: () => {
      setCurrent("");
      setNext("");
      toast.show({ title: t("security.password.changed"), tone: "success" });
    },
  });
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    mutation.mutate({ current_password: current, new_password: next });
  };
  return (
    <Card title={t("security.password.title")}>
      <form onSubmit={submit} className="flex flex-col gap-4" noValidate>
        <p className="text-sm text-fg-muted">{t("security.password.description")}</p>
        <FormError error={mutation.error} />
        <PasswordField
          label={t("auth.fields.currentPassword")}
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <PasswordField
          label={t("auth.fields.newPassword")}
          autoComplete="new-password"
          hint={t("auth.fields.passwordHint")}
          value={next}
          onChange={(e) => setNext(e.target.value)}
          showLabel={t("auth.fields.showPassword")}
          hideLabel={t("auth.fields.hidePassword")}
        />
        <Button type="submit" loading={mutation.isPending} disabled={!current || next.length < 10}>
          {t("security.password.submit")}
        </Button>
      </form>
    </Card>
  );
}

function PinCard() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const me = useMe();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [resetPin, setResetPin] = useState("");

  const refresh = () => queryClient.invalidateQueries({ queryKey: identityKeys.me });
  const change = useMutation({
    mutationFn: api.changePin,
    onSuccess: () => {
      setCurrent("");
      setNext("");
      toast.show({ title: t("pin.changed"), tone: "success" });
    },
    onSettled: refresh,
  });
  const requestReset = useMutation({ mutationFn: api.requestPinReset });
  const confirmReset = useMutation({
    mutationFn: api.confirmPinReset,
    onSuccess: () => {
      toast.show({ title: t("pin.resetDone"), tone: "success" });
      requestReset.reset();
      setPassword("");
      setCode("");
      setResetPin("");
    },
    onSettled: refresh,
  });

  const pin = me.data?.pin;
  const locked = Boolean(pin?.locked_until) || Boolean(pin?.requires_reset);

  return (
    <Card
      title={t("pin.title")}
      actions={
        locked ? (
          <Badge tone="danger">{t("pin.locked")}</Badge>
        ) : (
          <Badge tone="success">{t("pin.active")}</Badge>
        )
      }
    >
      <div className="flex flex-col gap-6">
        {!locked && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              change.mutate({ current_pin: current, new_pin: next });
            }}
            className="flex flex-col gap-4"
            noValidate
          >
            <FormError error={change.error} />
            <div className="grid gap-4 sm:grid-cols-2">
              <PinField label={t("pin.current")} value={current} onValueChange={setCurrent} />
              <PinField label={t("pin.new")} value={next} onValueChange={setNext} />
            </div>
            <Button
              type="submit"
              variant="secondary"
              loading={change.isPending}
              disabled={current.length !== 6 || next.length !== 6}
            >
              {t("pin.change")}
            </Button>
          </form>
        )}

        <div className="flex flex-col gap-4 border-t border-border pt-5">
          <h3 className="text-base font-semibold text-fg">{t("pin.resetTitle")}</h3>
          {pin?.requires_reset && (
            <InlineAlert tone="warning" title={t("errors.pinResetRequired")} />
          )}
          <FormError error={requestReset.error ?? confirmReset.error} />
          {!requestReset.isSuccess ? (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                requestReset.mutate(password);
              }}
              className="flex flex-col gap-4"
              noValidate
            >
              <PasswordField
                label={t("auth.fields.password")}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                showLabel={t("auth.fields.showPassword")}
                hideLabel={t("auth.fields.hidePassword")}
              />
              <Button
                type="submit"
                variant="ghost"
                loading={requestReset.isPending}
                disabled={!password}
              >
                {t("pin.sendResetCode")}
              </Button>
            </form>
          ) : (
            <form
              onSubmit={(e) => {
                e.preventDefault();
                confirmReset.mutate({ code, new_pin: resetPin });
              }}
              className="flex flex-col gap-4"
              noValidate
            >
              <InlineAlert tone="info" title={t("pin.codeSent")} />
              <div className="grid gap-4 sm:grid-cols-2">
                <PinField
                  label={t("auth.verify.code")}
                  masked={false}
                  value={code}
                  onValueChange={setCode}
                />
                <PinField label={t("pin.new")} value={resetPin} onValueChange={setResetPin} />
              </div>
              <Button
                type="submit"
                loading={confirmReset.isPending}
                disabled={code.length !== 6 || resetPin.length !== 6}
              >
                {t("pin.resetSubmit")}
              </Button>
            </form>
          )}
        </div>
      </div>
    </Card>
  );
}

function SessionsCard() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const format = useDateTime();
  const sessions = useQuery({ queryKey: identityKeys.sessions, queryFn: api.sessions });
  const revoke = useMutation({
    mutationFn: api.revokeSession,
    onSettled: () => queryClient.invalidateQueries({ queryKey: identityKeys.sessions }),
  });
  const logoutAll = useMutation({
    mutationFn: api.logoutAll,
    onSuccess: () => {
      queryClient.clear();
      void navigate("/login", { replace: true });
    },
  });

  return (
    <Card
      title={t("security.sessions.title")}
      actions={
        <Button
          size="sm"
          variant="danger"
          loading={logoutAll.isPending}
          onClick={() => logoutAll.mutate()}
        >
          {t("security.sessions.logoutAll")}
        </Button>
      }
    >
      {sessions.isPending ? (
        <div role="status" aria-label={t("security.loading")} className="flex flex-col gap-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : sessions.isError ? (
        <ErrorState
          title={t("status.errorTitle")}
          retryLabel={t("status.retry")}
          onRetry={() => void sessions.refetch()}
        />
      ) : (
        <ul className="divide-y divide-border">
          {sessions.data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-fg">
                  {s.user_agent ?? t("security.sessions.unknownDevice")}
                </p>
                <p className="text-xs text-fg-muted">
                  {s.ip ?? "—"} · {t("security.sessions.lastSeen")} {format(s.last_seen_at)}
                </p>
              </div>
              {s.current ? (
                <Badge tone="primary">{t("security.sessions.current")}</Badge>
              ) : (
                <Button
                  size="sm"
                  variant="secondary"
                  loading={revoke.isPending && revoke.variables === s.id}
                  onClick={() => revoke.mutate(s.id)}
                >
                  {t("security.sessions.revoke")}
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function LoginHistoryCard() {
  const { t } = useTranslation();
  const api = useIdentityApi();
  const format = useDateTime();
  const history = useQuery({ queryKey: identityKeys.loginHistory, queryFn: api.loginHistory });
  return (
    <Card title={t("security.history.title")}>
      {history.isPending ? (
        <Skeleton className="h-24 w-full" />
      ) : history.isError ? (
        <ErrorState
          title={t("status.errorTitle")}
          retryLabel={t("status.retry")}
          onRetry={() => void history.refetch()}
        />
      ) : history.data.length === 0 ? (
        <EmptyState title={t("security.history.empty")} />
      ) : (
        <ul className="divide-y divide-border">
          {history.data.map((item) => (
            <li
              key={item.at + item.result}
              className="flex items-center justify-between gap-3 py-2.5 text-sm"
            >
              <span className="tabular text-fg">{format(item.at)}</span>
              <span className="text-fg-muted">{item.ip ?? "—"}</span>
              <Badge tone={item.result === "SUCCESS" ? "success" : "danger"}>
                {t(`security.history.results.${item.result}`, { defaultValue: item.result })}
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

export function SecurityPage() {
  const { t } = useTranslation();
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-2">
        <h1 className="text-[1.75rem] font-bold text-fg">{t("security.title")}</h1>
        <p className="max-w-2xl text-fg-muted">{t("security.description")}</p>
      </header>
      <div className="grid gap-6 lg:grid-cols-2">
        <ChangePasswordCard />
        <PinCard />
        <SessionsCard />
        <LoginHistoryCard />
      </div>
    </div>
  );
}
