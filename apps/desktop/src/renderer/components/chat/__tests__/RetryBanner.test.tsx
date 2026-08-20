// @vitest-environment jsdom
import type { ErrorAction } from "@/lib/errors";
import {
  RECONNECTING_BANNER,
  RECONNECT_BANNER,
  RECONNECT_FINISHED_BANNER,
  RECONNECT_INTERRUPTED_BANNER,
  UNKNOWN_CLOUD_BANNER,
} from "@/services/turns/helpers";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { RetryBanner } from "../RetryBanner";

const clearError = vi.fn();
let error: string | null = null;
let action: ErrorAction | null = null;

vi.mock("@/stores/conversation", () => ({
  useActiveError: () => error,
  useActiveErrorAction: () => action,
  useConversationStore: (
    sel: (s: { clearError: typeof clearError }) => unknown,
  ) => sel({ clearError }),
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  clearError.mockReset();
  error = null;
  action = null;
});

describe("RetryBanner", () => {
  it("renders error copy and close, without 重试 or 重连", () => {
    error = "发送失败，请稍后重试";
    render(<RetryBanner />);
    expect(screen.getByText("发送失败，请稍后重试")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重连" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "关闭" }));
    expect(clearError).toHaveBeenCalledOnce();
  });

  it("shows confirmed-live reconnect copy without a 重连 button", () => {
    error = RECONNECT_BANNER;
    const { container } = render(<RetryBanner />);
    expect(screen.getByText(RECONNECT_BANNER)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "重连" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(container.firstElementChild?.getAttribute("data-banner-tone")).toBe(
      "notice",
    );
  });

  it("uses notice chrome for quiet reconnect / finished copy", () => {
    for (const copy of [RECONNECTING_BANNER, RECONNECT_FINISHED_BANNER]) {
      error = copy;
      const { container, unmount } = render(<RetryBanner />);
      expect(screen.getByText(copy)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "重连" })).toBeNull();
      expect(
        container.firstElementChild?.getAttribute("data-banner-tone"),
      ).toBe("notice");
      expect(container.firstElementChild?.className).toContain("bg-muted/40");
      unmount();
    }
  });

  it("uses alert chrome for interrupted / unknown-cloud copy", () => {
    for (const copy of [RECONNECT_INTERRUPTED_BANNER, UNKNOWN_CLOUD_BANNER]) {
      error = copy;
      const { container, unmount } = render(<RetryBanner />);
      expect(screen.getByText(copy)).toBeTruthy();
      expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
      expect(screen.queryByRole("button", { name: "重连" })).toBeNull();
      expect(
        container.firstElementChild?.getAttribute("data-banner-tone"),
      ).toBe("alert");
      expect(container.firstElementChild?.className).toContain("bg-muted/40");
      unmount();
    }
  });

  it("renders nothing when there is no error", () => {
    error = null;
    const { container } = render(<RetryBanner />);
    expect(container.firstChild).toBeNull();
  });

  it("uses neutral chrome for a recoverable interruption", () => {
    error = "上游限流，暂时无法继续本回合。请约 2 秒后再试。";
    const { container } = render(<RetryBanner />);
    const banner = container.firstElementChild as HTMLElement;
    expect(banner.className).toContain("bg-muted/40");
    expect(banner.className).not.toContain("destructive");
  });

  it("uses primary chrome when a config action is offered", () => {
    error = "请先接入自己的 API Key，再发起对话。";
    action = { label: "去服务商", href: "/more/providers" };
    const { container } = render(<RetryBanner />);
    const banner = container.firstElementChild as HTMLElement;
    expect(banner.className).toContain("bg-primary/10");
    expect(banner.className).not.toContain("destructive");
    expect(screen.getByRole("button", { name: "去服务商" })).toBeTruthy();
  });
});
