import {
  moneyKeys,
  type NotificationItem,
  useMe,
  useMoneyApi,
  useNotifications,
  useUnreadCount,
} from "@viewallet/api-client";
import { Button, Card, EmptyState, ErrorState, Skeleton } from "@viewallet/ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { clsx } from "clsx";
import { Bell } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";

import { formatDateTime } from "../features/wallet/format";

/** Header bell with the unread count (polled every 30 s while signed in). */
export function NotificationBell() {
  const { t } = useTranslation();
  const me = useMe();
  const unread = useUnreadCount(Boolean(me.data));
  if (!me.data) return null;
  const count = unread.data?.unread ?? 0;
  return (
    <Link
      to="/notifications"
      aria-label={count ? t("notifications.unread", { count }) : t("notifications.title")}
      className="relative grid size-9 place-items-center rounded-full text-fg-muted hover:bg-bg-subtle hover:text-fg"
    >
      <Bell aria-hidden className="size-5" />
      {count > 0 && (
        <span className="absolute -right-0.5 -top-0.5 grid min-w-5 place-items-center rounded-full bg-danger-500 px-1 text-[0.7rem] font-semibold leading-5 text-white">
          {count > 99 ? "99+" : count}
        </span>
      )}
    </Link>
  );
}

function Row({
  item,
  onOpen,
}: {
  item: NotificationItem;
  onOpen: (item: NotificationItem) => void;
}) {
  const { i18n } = useTranslation();
  return (
    <li>
      <button
        type="button"
        onClick={() => onOpen(item)}
        className={clsx(
          "flex w-full flex-col gap-0.5 rounded-md px-2 py-3 text-left hover:bg-bg-subtle",
          !item.read_at && "bg-primary-soft/40",
        )}
      >
        <span className="flex items-center gap-2 text-sm font-medium text-fg">
          {!item.read_at && <span aria-hidden className="size-2 rounded-full bg-primary" />}
          {item.title}
        </span>
        <span className="text-sm text-fg-muted">{item.body}</span>
        <time dateTime={item.created_at} className="text-xs text-fg-muted">
          {formatDateTime(item.created_at, i18n.language)}
        </time>
      </button>
    </li>
  );
}

export function NotificationsPage() {
  const { t } = useTranslation();
  const api = useMoneyApi();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const list = useNotifications();
  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: moneyKeys.notifications });
  };
  const markAll = useMutation({ mutationFn: api.markAllRead, onSuccess: refresh });
  const open = async (item: NotificationItem) => {
    if (!item.read_at) {
      await api.markRead(item.id);
      await refresh();
    }
    const link = typeof item.data.link === "string" ? item.data.link : null;
    if (link?.startsWith("/")) void navigate(link);
  };
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-2xl font-bold text-fg">{t("notifications.title")}</h1>
        <Button
          size="sm"
          variant="secondary"
          loading={markAll.isPending}
          onClick={() => markAll.mutate()}
        >
          {t("notifications.markAll")}
        </Button>
      </header>
      <Card>
        {list.isPending ? (
          <Skeleton className="h-24 w-full" />
        ) : list.isError ? (
          <ErrorState title={t("notifications.error")} onRetry={() => void list.refetch()} />
        ) : list.data.items.length === 0 ? (
          <EmptyState title={t("notifications.empty")} />
        ) : (
          <ul className="flex flex-col">
            {list.data.items.map((item) => (
              <Row key={item.id} item={item} onOpen={(i) => void open(i)} />
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
