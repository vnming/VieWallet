import * as RadixDialog from "@radix-ui/react-dialog";
import { clsx } from "clsx";
import { X } from "lucide-react";
import type { ReactNode } from "react";

export interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children?: ReactNode;
  footer?: ReactNode;
  closeLabel?: string;
}

/** Accessible modal dialog (focus trap, Escape to close, labelled by its title). */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  footer,
  closeLabel = "Đóng",
}: DialogProps) {
  return (
    <RadixDialog.Root open={open} onOpenChange={onOpenChange}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fixed inset-0 z-40 bg-ink-950/50 animate-fade-in" />
        <RadixDialog.Content
          className={clsx(
            "fixed z-50 w-full bg-surface-raised p-6 shadow-overlay animate-fade-in",
            "inset-x-0 bottom-0 rounded-t-xl md:inset-auto md:left-1/2 md:top-1/2 md:max-w-md",
            "md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-xl",
          )}
          {...(description ? {} : { "aria-describedby": undefined })}
        >
          <div className="flex items-start justify-between gap-4">
            <RadixDialog.Title className="text-lg font-semibold text-fg">{title}</RadixDialog.Title>
            <RadixDialog.Close
              className="rounded-sm p-1 text-fg-muted hover:text-fg"
              aria-label={closeLabel}
            >
              <X aria-hidden className="size-5" />
            </RadixDialog.Close>
          </div>
          {description && (
            <RadixDialog.Description className="mt-1 text-sm text-fg-muted">
              {description}
            </RadixDialog.Description>
          )}
          {children && <div className="mt-4">{children}</div>}
          {footer && <div className="mt-6 flex justify-end gap-2">{footer}</div>}
        </RadixDialog.Content>
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}
