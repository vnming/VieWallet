import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

// Intl inserts NBSP (U+00A0) and narrow NBSP (U+202F); normalize them for assertions.
const INTL_SPACES = new RegExp(`[${String.fromCharCode(0xa0, 0x202f)}]`, "g");

import {
  AmountText,
  Button,
  Dialog,
  ErrorState,
  StatusBadge,
  TextField,
  TRANSACTION_STATUSES,
  ToastProvider,
  useToast,
} from "../index";

const plain = (s: string | null) => (s ?? "").replace(INTL_SPACES, " ");

describe("Button", () => {
  it("calls onClick", async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Gửi</Button>);
    await userEvent.click(screen.getByRole("button", { name: "Gửi" }));
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("is disabled and busy while loading", async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Xác nhận
      </Button>,
    );
    const button = screen.getByRole("button", { name: "Xác nhận" });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    await userEvent.click(button);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("defaults to type=button so it never submits forms by accident", () => {
    render(<Button>OK</Button>);
    expect(screen.getByRole("button")).toHaveAttribute("type", "button");
  });
});

describe("TextField", () => {
  it("links label, hint and error for assistive technology", () => {
    render(<TextField label="Số tiền" hint="Tối thiểu 1.000 ₫" error="Chỉ nhập chữ số" />);
    const input = screen.getByLabelText("Số tiền");
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription("Tối thiểu 1.000 ₫ Chỉ nhập chữ số");
  });

  it("is valid without an error", () => {
    render(<TextField label="Email" />);
    expect(screen.getByLabelText("Email")).not.toHaveAttribute("aria-invalid");
  });
});

describe("StatusBadge", () => {
  it.each(TRANSACTION_STATUSES)("renders a text label for %s", (status) => {
    const { container } = render(<StatusBadge status={status} />);
    expect(container.textContent.trim()).not.toBe("");
    // The icon is decorative; the meaning is carried by text, not colour.
    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("localizes labels", () => {
    render(<StatusBadge status="COMPLETED" locale="en" />);
    expect(screen.getByText("Completed")).toBeInTheDocument();
    render(<StatusBadge status="COMPLETED" />);
    expect(screen.getByText("Thành công")).toBeInTheDocument();
  });
});

describe("AmountText", () => {
  it("prefixes incoming money with + and outgoing with a minus sign", () => {
    const { rerender, container } = render(
      <AmountText money={{ amount: "250000", currency: "VND" }} direction="IN" />,
    );
    expect(plain(container.textContent)).toBe("+250.000 ₫");
    rerender(<AmountText money={{ amount: "1100", currency: "VND" }} direction="OUT" />);
    expect(plain(container.textContent)).toBe("−1.100 ₫");
  });

  it("uses tabular digits", () => {
    const { container } = render(<AmountText money={{ amount: "0", currency: "VND" }} />);
    expect(container.firstChild).toHaveClass("tabular");
  });
});

describe("ErrorState", () => {
  it("shows the correlation id and retries", async () => {
    const onRetry = vi.fn();
    render(<ErrorState title="Lỗi" correlationId="abc123" onRetry={onRetry} />);
    expect(screen.getByRole("alert")).toHaveTextContent("abc123");
    await userEvent.click(screen.getByRole("button", { name: "Thử lại" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });
});

describe("Toast", () => {
  function Trigger() {
    const toast = useToast();
    return (
      <Button onClick={() => toast.show({ title: "Đã lưu", tone: "success", duration: 1000 })}>
        show
      </Button>
    );
  }

  it("announces a toast and dismisses it after its duration", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      render(
        <ToastProvider>
          <Trigger />
        </ToastProvider>,
      );
      await userEvent.click(screen.getByRole("button", { name: "show" }));
      expect(screen.getByRole("status")).toHaveTextContent("Đã lưu");
      act(() => {
        vi.advanceTimersByTime(1100);
      });
      expect(screen.queryByText("Đã lưu")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("can be closed manually", async () => {
    render(
      <ToastProvider>
        <Trigger />
      </ToastProvider>,
    );
    await userEvent.click(screen.getByRole("button", { name: "show" }));
    await userEvent.click(screen.getByRole("button", { name: "Đóng" }));
    expect(screen.queryByText("Đã lưu")).not.toBeInTheDocument();
  });

  it("throws outside the provider", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Trigger />)).toThrow("ToastProvider");
  });
});

describe("Dialog", () => {
  it("is labelled by its title and closes with Escape", async () => {
    const onOpenChange = vi.fn();
    render(
      <Dialog open onOpenChange={onOpenChange} title="Xác nhận chuyển tiền" description="Mô tả" />,
    );
    expect(screen.getByRole("dialog", { name: "Xác nhận chuyển tiền" })).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });
});
