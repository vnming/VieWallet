import {
  identityKeys,
  spendKeys,
  useAccountApi,
  useIdentityApi,
  useMe,
  useRecipients,
  useSpendApi,
} from "@viewallet/api-client";
import { isLocale, rememberLocale } from "@viewallet/shared";
import {
  Badge,
  Button,
  Card,
  EmptyState,
  ErrorState,
  InlineAlert,
  Skeleton,
  TextField,
  useToast,
} from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Banknote,
  ChevronRight,
  Gauge,
  KeyRound,
  LifeBuoy,
  type LucideIcon,
  MessageSquare,
  Settings,
  UserRound,
  Users,
} from "lucide-react";
import { type SyntheticEvent, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";

import { FormError } from "../../features/auth/FormError";
import { PageHeader } from "../../features/money/fields";
import { applyTheme, type Theme } from "../../features/preferences/theme";

interface HubLink {
  to: string;
  key: string;
  icon: LucideIcon;
}

const HUB: HubLink[] = [
  { to: "/account/profile", key: "profile", icon: UserRound },
  { to: "/settings", key: "settings", icon: Settings },
  { to: "/account/security", key: "security", icon: KeyRound },
  { to: "/kyc", key: "kyc", icon: BadgeCheck },
  { to: "/wallet/limits", key: "limits", icon: Gauge },
  { to: "/wallet/destinations", key: "destinations", icon: Banknote },
  { to: "/account/recipients", key: "recipients", icon: Users },
  { to: "/support/tickets", key: "support", icon: MessageSquare },
  { to: "/help", key: "help", icon: LifeBuoy },
];

export function AccountHubPage() {
  const { t } = useTranslation();
  const me = useMe();
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("account.title")} description={t("account.description")} />
      <Card>
        {me.isPending ? (
          <Skeleton className="h-12 w-full" />
        ) : me.data ? (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <span className="flex flex-col">
              <span className="text-lg font-semibold text-fg">
                {me.data.display_name ?? me.data.full_name}
              </span>
              <span className="text-sm text-fg-muted">{me.data.email}</span>
            </span>
            <Badge tone={me.data.kyc_tier >= 1 ? "success" : "neutral"}>
              {t("limits.tier", { tier: me.data.kyc_tier })}
            </Badge>
          </div>
        ) : (
          <ErrorState title={t("errors.generic")} onRetry={() => void me.refetch()} />
        )}
      </Card>
      <nav aria-label={t("account.title")}>
        <ul className="grid gap-3 md:grid-cols-2">
          {HUB.map(({ to, key, icon: Icon }) => (
            <li key={key}>
              <Link
                to={to}
                className="flex items-center gap-3 rounded-lg border border-border bg-surface p-4 hover:bg-bg-subtle focus-visible:outline-2 focus-visible:outline-primary"
              >
                <Icon aria-hidden className="size-5 text-primary" />
                <span className="flex flex-1 flex-col">
                  <span className="text-sm font-semibold text-fg">{t(`account.hub.${key}`)}</span>
                  <span className="text-xs text-fg-muted">{t(`account.hubHint.${key}`)}</span>
                </span>
                <ChevronRight aria-hidden className="size-4 text-fg-muted" />
              </Link>
            </li>
          ))}
        </ul>
      </nav>
    </div>
  );
}

// ------------------------------------------------------------------ profile

function EmailChangeCard() {
  const { t } = useTranslation();
  const api = useAccountApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const request = useMutation({ mutationFn: () => api.requestEmailChange(password, email) });
  const confirm = useMutation({
    mutationFn: () => api.confirmEmailChange(code),
    onSuccess: async () => {
      toast.show({ title: t("profile.emailChanged"), tone: "success" });
      setEmail("");
      setPassword("");
      setCode("");
      request.reset();
      await queryClient.invalidateQueries({ queryKey: identityKeys.me });
    },
  });
  return (
    <Card title={t("profile.emailTitle")}>
      {!request.isSuccess ? (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            request.mutate();
          }}
        >
          <TextField
            label={t("profile.newEmail")}
            type="email"
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value.trim())}
          />
          <TextField
            label={t("profile.password")}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <FormError error={request.error} />
          <Button
            type="submit"
            loading={request.isPending}
            disabled={!email.includes("@") || password.length < 8}
            className="w-fit"
          >
            {t("profile.sendCode")}
          </Button>
        </form>
      ) : (
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            confirm.mutate();
          }}
        >
          <InlineAlert tone="info" title={t("profile.codeSent", { email })} />
          <TextField
            label={t("profile.code")}
            inputMode="numeric"
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 6))}
          />
          <FormError error={confirm.error} />
          <Button
            type="submit"
            loading={confirm.isPending}
            disabled={code.length !== 6}
            className="w-fit"
          >
            {t("profile.confirmEmail")}
          </Button>
        </form>
      )}
    </Card>
  );
}

export function ProfilePage() {
  const { t } = useTranslation();
  const me = useMe();
  const api = useIdentityApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<{
    full_name: string;
    display_name: string;
    phone: string;
  } | null>(null);
  const save = useMutation({
    mutationFn: api.updateProfile,
    onSuccess: async () => {
      toast.show({ title: t("profile.saved"), tone: "success" });
      setForm(null);
      await queryClient.invalidateQueries({ queryKey: identityKeys.me });
    },
  });
  if (me.isPending) return <Skeleton className="h-64 w-full" />;
  if (!me.data) {
    return <ErrorState title={t("errors.generic")} onRetry={() => void me.refetch()} />;
  }
  const data = me.data;
  const locked = data.kyc_tier >= 1;
  const values = form ?? {
    full_name: data.full_name,
    display_name: data.display_name ?? "",
    phone: data.phone ?? "",
  };
  const submit = (event: SyntheticEvent) => {
    event.preventDefault();
    save.mutate({
      ...(locked ? {} : { full_name: values.full_name }),
      display_name: values.display_name,
      phone: values.phone,
    });
  };
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("profile.title")} description={t("profile.description")} />
      <Card>
        <form onSubmit={submit} className="grid gap-4 md:grid-cols-2">
          <TextField
            label={t("profile.fullName")}
            autoComplete="name"
            value={values.full_name}
            disabled={locked}
            hint={locked ? t("profile.nameLocked") : undefined}
            onChange={(e) => setForm({ ...values, full_name: e.target.value })}
          />
          <TextField
            label={t("profile.displayName")}
            autoComplete="nickname"
            maxLength={60}
            value={values.display_name}
            onChange={(e) => setForm({ ...values, display_name: e.target.value })}
          />
          <TextField
            label={t("profile.phone")}
            type="tel"
            inputMode="tel"
            autoComplete="tel"
            hint={t("profile.phoneHint")}
            value={values.phone}
            onChange={(e) => setForm({ ...values, phone: e.target.value })}
          />
          <div className="flex flex-col justify-end gap-1 text-sm">
            <span className="text-fg-muted">{t("profile.email")}</span>
            <span className="font-medium text-fg">{data.email}</span>
          </div>
          <div className="flex flex-col gap-3 md:col-span-2">
            <FormError error={save.error} />
            <Button
              type="submit"
              loading={save.isPending}
              disabled={form === null}
              className="w-fit"
            >
              {t("profile.save")}
            </Button>
          </div>
        </form>
      </Card>
      <EmailChangeCard />
    </div>
  );
}

// ------------------------------------------------------------------ settings

const THEMES: Theme[] = ["SYSTEM", "LIGHT", "DARK"];

export function SettingsPage() {
  const { t, i18n } = useTranslation();
  const me = useMe();
  // The checkbox follows the click at once; the server copy catches up in the background.
  const [privacy, setPrivacy] = useState<boolean | null>(null);
  const api = useIdentityApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: api.updateProfile,
    // Optimistic: the control reflects the choice at once; an error restores the server value.
    onMutate: (changes: Parameters<typeof api.updateProfile>[0]) => {
      // A /me request still in flight would overwrite the optimistic value when it lands.
      void queryClient.cancelQueries({ queryKey: identityKeys.me });
      const current = queryClient.getQueryData<typeof me.data>(identityKeys.me);
      if (current) queryClient.setQueryData(identityKeys.me, { ...current, ...changes });
      if (changes.theme) applyTheme(changes.theme);
    },
    onError: () => {
      setPrivacy(null);
      void queryClient.invalidateQueries({ queryKey: identityKeys.me });
    },
    onSuccess: async (updated, changes) => {
      toast.show({ title: t("settings.saved"), tone: "success" });
      applyTheme(updated.theme);
      if (isLocale(updated.locale) && updated.locale !== i18n.language) {
        rememberLocale(updated.locale);
        await i18n.changeLanguage(updated.locale);
      }
      // Only the fields this call changed: another setting saved meanwhile must not revert.
      const fields = Object.keys(changes) as (keyof typeof changes)[];
      queryClient.setQueryData<typeof updated>(identityKeys.me, (current) =>
        current
          ? { ...current, ...Object.fromEntries(fields.map((f) => [f, updated[f]])) }
          : updated,
      );
    },
  });
  if (me.isPending) return <Skeleton className="h-64 w-full" />;
  if (!me.data) {
    return <ErrorState title={t("errors.generic")} onRetry={() => void me.refetch()} />;
  }
  const data = me.data;
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("settings.title")} description={t("settings.description")} />
      <Card title={t("settings.language")}>
        <div role="radiogroup" aria-label={t("settings.language")} className="flex gap-2">
          {(["vi", "en"] as const).map((locale) => (
            <Button
              key={locale}
              role="radio"
              aria-checked={data.locale === locale}
              variant={data.locale === locale ? "primary" : "secondary"}
              size="sm"
              onClick={() => save.mutate({ locale })}
            >
              {t(`language.${locale}`)}
            </Button>
          ))}
        </div>
      </Card>
      <Card title={t("settings.theme")}>
        <div role="radiogroup" aria-label={t("settings.theme")} className="flex flex-wrap gap-2">
          {THEMES.map((theme) => (
            <Button
              key={theme}
              role="radio"
              aria-checked={data.theme === theme}
              variant={data.theme === theme ? "primary" : "secondary"}
              size="sm"
              onClick={() => save.mutate({ theme })}
            >
              {t(`settings.themes.${theme}`)}
            </Button>
          ))}
        </div>
      </Card>
      <Card title={t("settings.privacy")}>
        <label className="flex items-start gap-3 text-sm text-fg">
          <input
            type="checkbox"
            className="mt-1 size-4"
            checked={privacy ?? data.privacy_mode}
            onChange={(e) => {
              setPrivacy(e.target.checked);
              save.mutate({ privacy_mode: e.target.checked });
            }}
          />
          <span className="flex flex-col">
            <span className="font-medium">{t("settings.privacyLabel")}</span>
            <span className="text-fg-muted">{t("settings.privacyHint")}</span>
          </span>
        </label>
      </Card>
      <FormError error={save.error} />
    </div>
  );
}

// ------------------------------------------------------------------ saved recipients

export function RecipientsPage() {
  const { t } = useTranslation();
  const recipients = useRecipients();
  const api = useSpendApi();
  const toast = useToast();
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: api.removeRecipient,
    onSuccess: async () => {
      toast.show({ title: t("recipients.removed"), tone: "success" });
      await queryClient.invalidateQueries({ queryKey: spendKeys.recipients });
    },
  });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title={t("recipients.title")} description={t("recipients.description")} />
      <Card>
        {recipients.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : recipients.isError ? (
          <ErrorState title={t("errors.generic")} onRetry={() => void recipients.refetch()} />
        ) : recipients.data.length === 0 ? (
          <EmptyState
            title={t("recipients.empty")}
            action={
              <Link to="/transfer" className="font-medium text-primary underline">
                {t("transfer.title")}
              </Link>
            }
          />
        ) : (
          <ul className="divide-y divide-border">
            {recipients.data.map((r) => (
              <li key={r.id} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <span className="flex flex-col">
                  <span className="text-sm font-medium text-fg">
                    {r.nickname ?? r.recipient.display_name}
                  </span>
                  <span className="text-xs text-fg-muted">
                    {r.recipient.display_name} · {r.recipient.wallet_number_masked}
                  </span>
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  loading={remove.isPending && remove.variables === r.id}
                  onClick={() => remove.mutate(r.id)}
                  aria-label={t("recipients.removeOf", {
                    name: r.nickname ?? r.recipient.display_name,
                  })}
                >
                  {t("recipients.remove")}
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
