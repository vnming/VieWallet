import {
  Ban,
  CheckCircle2,
  CircleDashed,
  Clock,
  Loader,
  type LucideIcon,
  RotateCcw,
  TimerOff,
  Undo2,
  XCircle,
} from "lucide-react";

import { Badge, type BadgeTone } from "./Badge";

/** Transaction statuses (docs/architecture/financial-processing.md §5.1). */
export const TRANSACTION_STATUSES = [
  "CREATED",
  "PENDING",
  "PROCESSING",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "EXPIRED",
  "PARTIALLY_REFUNDED",
  "REFUNDED",
  "REVERSED",
] as const;
export type TransactionStatus = (typeof TRANSACTION_STATUSES)[number];

interface StatusStyle {
  icon: LucideIcon;
  tone: BadgeTone;
  label: { vi: string; en: string };
}

export const STATUS_STYLES: Record<TransactionStatus, StatusStyle> = {
  CREATED: {
    icon: CircleDashed,
    tone: "neutral",
    label: { vi: "Chờ xác nhận", en: "Awaiting confirmation" },
  },
  PENDING: { icon: Clock, tone: "warning", label: { vi: "Đang chờ xử lý", en: "Pending" } },
  PROCESSING: { icon: Loader, tone: "info", label: { vi: "Đang xử lý", en: "Processing" } },
  COMPLETED: { icon: CheckCircle2, tone: "success", label: { vi: "Thành công", en: "Completed" } },
  FAILED: { icon: XCircle, tone: "danger", label: { vi: "Thất bại", en: "Failed" } },
  CANCELLED: { icon: Ban, tone: "neutral", label: { vi: "Đã hủy", en: "Cancelled" } },
  EXPIRED: { icon: TimerOff, tone: "neutral", label: { vi: "Hết hạn", en: "Expired" } },
  PARTIALLY_REFUNDED: {
    icon: RotateCcw,
    tone: "info",
    label: { vi: "Hoàn một phần", en: "Partially refunded" },
  },
  REFUNDED: { icon: RotateCcw, tone: "info", label: { vi: "Đã hoàn tiền", en: "Refunded" } },
  REVERSED: { icon: Undo2, tone: "warning", label: { vi: "Đã đảo giao dịch", en: "Reversed" } },
};

export interface StatusBadgeProps {
  status: TransactionStatus;
  locale?: "vi" | "en";
}

/** Status is always shown as icon + text, never by colour alone. */
export function StatusBadge({ status, locale = "vi" }: StatusBadgeProps) {
  const style = STATUS_STYLES[status];
  const Icon = style.icon;
  return (
    <Badge tone={style.tone} icon={<Icon aria-hidden className="size-3.5" />}>
      {style.label[locale]}
    </Badge>
  );
}
