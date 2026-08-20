// @vitest-environment jsdom
import type { ErrorAction } from "@/lib/errors";
import {
  RECONNECTING_BANNER,
  RECONNECT_INTERRUPTED_BANNER,
} from "@/services/turns/helpers";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ComposerSendErrorNotice } from "../ComposerSendErrorNotice";

let composerError: {
  message: string;
  action: ErrorAction | null;
  supportPack?: {
    conversationId?: string;
    userMessageId?: string;
    messageId?: string;
    errorCode?: string;
  };
} | null = null;
let sessionError: string | null = null;
let sessionAction: ErrorAction | null = null;

vi.mock("@/stores/composerSendError", () => ({
  useComposerSendError: () => composerError,
  clearComposerSendError: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useActiveError: () => sessionError,
  useActiveErrorAction: () => sessionAction,
  useConversationStore: {
    getState: () => ({ clearError: vi.fn() }),
  },
}));

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

afterEach(() => {
  cleanup();
  composerError = null;
  sessionError = null;
  sessionAction = null;
});

describe("ComposerSendErrorNotice", () => {
  it("uses neutral chrome for a rate-limit refusal", () => {
    composerError = {
      message: "上游限流，暂时无法继续本回合。请约 2 秒后再试。",
      action: null,
    };
    render(<ComposerSendErrorNotice draftKey="__draft__" />);
    const banner = screen.getByTestId("composer-send-error");
    expect(banner.className).toContain("bg-muted/40");
    expect(banner.className).not.toContain("destructive");
  });

  it("uses notice chrome for a quiet reconnect session banner", () => {
    sessionError = RECONNECTING_BANNER;
    render(<ComposerSendErrorNotice draftKey="__draft__" />);
    const banner = screen.getByTestId("composer-send-error");
    expect(banner.getAttribute("data-banner-tone")).toBe("notice");
    expect(banner.className).toContain("bg-muted/40");
  });

  it("uses alert chrome for an interrupted session banner", () => {
    sessionError = RECONNECT_INTERRUPTED_BANNER;
    render(<ComposerSendErrorNotice draftKey="__draft__" />);
    const banner = screen.getByTestId("composer-send-error");
    expect(banner.getAttribute("data-banner-tone")).toBe("alert");
    expect(banner.className).toContain("bg-muted/40");
  });

  it("uses primary chrome when a config action is offered", () => {
    composerError = {
      message: "请先接入自己的 API Key，再发起对话。",
      action: { label: "去服务商", href: "/more/providers" },
    };
    render(<ComposerSendErrorNotice draftKey="__draft__" />);
    const banner = screen.getByTestId("composer-send-error");
    expect(banner.className).toContain("bg-primary/10");
    expect(banner.className).not.toContain("destructive");
    expect(screen.getByRole("button", { name: "去服务商" })).toBeTruthy();
  });

  it("suppressSession hides sessionError", () => {
    sessionError = "网络中断，请重试。";
    render(<ComposerSendErrorNotice draftKey="__draft__" suppressSession />);
    expect(screen.queryByTestId("composer-send-error")).toBeNull();
  });

  it("composerError still shows when session is suppressed", () => {
    sessionError = "网络中断，请重试。";
    composerError = { message: "发送失败，请稍后重试", action: null };
    render(<ComposerSendErrorNotice draftKey="__draft__" suppressSession />);
    expect(screen.getByTestId("composer-send-error").textContent).toContain(
      "发送失败，请稍后重试",
    );
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("session host can hang 复制排查包", () => {
    sessionError = "网络中断，请重试。";
    const onCopy = vi.fn();
    render(
      <ComposerSendErrorNotice
        draftKey="__draft__"
        onCopySupportPack={onCopy}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "复制排查包" }));
    expect(onCopy).toHaveBeenCalled();
  });

  it("composerError does not show the session 复制排查包", () => {
    composerError = { message: "发送失败，请稍后重试", action: null };
    render(
      <ComposerSendErrorNotice
        draftKey="__draft__"
        onCopySupportPack={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("composerError with supportPack hosts 复制排查包", () => {
    composerError = {
      message: "上游限流，暂时无法继续本回合。",
      action: null,
      supportPack: {
        conversationId: "c1",
        userMessageId: "u1",
        messageId: "a1",
        errorCode: "LLM_RATE_LIMIT",
      },
    };
    render(<ComposerSendErrorNotice draftKey="__draft__" />);
    expect(screen.getByRole("button", { name: "复制排查包" })).toBeTruthy();
  });
});
