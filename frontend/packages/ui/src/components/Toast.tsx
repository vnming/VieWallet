import { clsx } from "clsx";
import { CheckCircle2, Info, TriangleAlert, X, XCircle } from "lucide-react";
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

export type ToastTone = "info" | "success" | "warning" | "danger";

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** Milliseconds before auto-dismiss; 0 keeps it until closed. */
  duration?: number;
}

interface ToastItem extends Required<Omit<ToastOptions, "description">> {
  id: number;
  description?: string | undefined;
}

interface ToastContextValue {
  show: (options: ToastOptions) => number;
  dismiss: (id: number) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const icons = { info: Info, success: CheckCircle2, warning: TriangleAlert, danger: XCircle };
const toneClasses: Record<ToastTone, string> = {
  info: "text-info-700 dark:text-info-500",
  success: "text-success-700 dark:text-success-500",
  warning: "text-warning-700 dark:text-warning-500",
  danger: "text-danger-700 dark:text-danger-500",
};

export function ToastProvider({
  children,
  closeLabel = "Đóng",
}: {
  children: ReactNode;
  closeLabel?: string;
}) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());

  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer) clearTimeout(timer);
    timers.current.delete(id);
    setItems((current) => current.filter((item) => item.id !== id));
  }, []);

  const show = useCallback(
    ({ title, description, tone = "info", duration = 5000 }: ToastOptions) => {
      const id = nextId.current++;
      setItems((current) => [...current.slice(-3), { id, title, description, tone, duration }]);
      if (duration > 0) {
        timers.current.set(
          id,
          setTimeout(() => dismiss(id), duration),
        );
      }
      return id;
    },
    [dismiss],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach(clearTimeout);
  }, []);

  const value = useMemo(() => ({ show, dismiss }), [show, dismiss]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Polite live region: announced by screen readers without stealing focus. */}
      <div
        aria-live="polite"
        aria-relevant="additions"
        className="pointer-events-none fixed inset-x-0 bottom-20 z-50 flex flex-col items-center gap-2 px-4 md:bottom-6 md:items-end md:px-6"
      >
        {items.map((item) => {
          const Icon = icons[item.tone];
          return (
            <div
              key={item.id}
              role={item.tone === "danger" ? "alert" : "status"}
              className="pointer-events-auto flex w-full max-w-sm items-start gap-3 rounded-lg border border-border bg-surface-raised p-4 shadow-overlay animate-fade-in"
            >
              <Icon
                aria-hidden
                className={clsx("mt-0.5 size-5 shrink-0", toneClasses[item.tone])}
              />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold text-fg">{item.title}</p>
                {item.description && (
                  <p className="mt-0.5 text-sm text-fg-muted">{item.description}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => dismiss(item.id)}
                className="rounded-sm p-0.5 text-fg-muted hover:text-fg"
                aria-label={closeLabel}
              >
                <X aria-hidden className="size-4" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside <ToastProvider>");
  return context;
}
