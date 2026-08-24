// @vitest-environment jsdom
/**
 * 平台模式 503：运营方 Sub2API 账号的诊断一个字都不进气泡。
 *
 * 平台模式意味着用户根本没有自己的 key，那几句（OAuth 过期需重新登录 ChatGPT /
 * 账号 xxx@gmail.com 被上游拒绝 / 未绑定 access token）说的全是运营方的账号：
 * 用户会真去登录 ChatGPT、翻遍设置找不存在的绑定入口、被陌生人邮箱吓到。后端已
 * 改为只写日志，这里钉住即便老 journal 仍带着这段 context，渲染层也不会拼出来。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    useActiveGenerating: () => false,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: "conv-1" }),
    getActiveRuntime: () => ({ messages: [] }),
    assistantProjectionId: (m: { id: string }) => m.id,
  };
});

vi.mock("@/stores/usage", () => ({
  useUsageStore: (
    sel: (s: {
      loadMessageCost: () => void;
      messageCosts: Record<string, never>;
    }) => unknown,
  ) => sel({ loadMessageCost: () => {}, messageCosts: {} }),
}));

vi.mock("@/stores/execution", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/execution")>();
  return {
    ...actual,
    useExecutionStore: (
      sel: (s: { byId: Record<string, { deliveryStatus: null }> }) => unknown,
    ) => sel({ byId: {} }),
    useMessageExecution: () => null,
  };
});

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: [],
      planReviews: [],
    }),
  };
});

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("../AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <div data-testid="assistant-footer" />,
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => (
    <div data-testid="assistant-body">{content}</div>
  ),
}));

vi.mock("@/components/chat/debate/CollapsibleSpeech", () => ({
  CollapsibleSpeech: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { AssistantMessage } from "../AssistantMessage";

const UPSTREAM_503 = "上游模型服务暂时不可用（503），请稍后再试";

/** 老 journal 里可能残留的运营方诊断字段（后端已不再下发）。 */
const LEGACY_OPERATOR_CONTEXT = {
  upstream_status: 503,
  credential_source: "platform",
  sub2api_diagnosis:
    "OAuth token 已过期（到期时间 09:41），需要重新登录 ChatGPT",
  sub2api_account: "eli***@gmail.com",
} as NonNullable<NonNullable<Message["error"]>["context"]>;

function failedMessage(): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "",
    createdAt: "2026-08-13T00:00:00Z",
    executionId: null,
    isStreaming: false,
    finishReason: "error",
    error: {
      code: "LLM_UPSTREAM_ERROR",
      message: UPSTREAM_503,
      context: LEGACY_OPERATOR_CONTEXT,
    },
  };
}

function renderBubble(message: Message) {
  return render(
    <MemoryRouter>
      <TooltipProvider>
        <AssistantMessage message={message} />
      </TooltipProvider>
    </MemoryRouter>,
  );
}

afterEach(cleanup);

describe("AssistantMessage platform 503 error card", () => {
  it("只说上游暂时不可用，不转述运营方账号的诊断", () => {
    const { container } = renderBubble(failedMessage());

    expect(screen.getByText(UPSTREAM_503)).toBeTruthy();

    const shown = container.textContent ?? "";
    for (const operatorLeak of [
      "诊断",
      "OAuth",
      "ChatGPT",
      "重新登录",
      "eli***@gmail.com",
      "@gmail.com",
      "access token",
      "被上游拒绝",
    ]) {
      expect(shown).not.toContain(operatorLeak);
    }
  });

  it("不挂重试按钮（定案 A），文案也不点名一个不存在的按钮", () => {
    const { container } = renderBubble(failedMessage());

    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    expect(container.textContent ?? "").not.toContain("点重试");
    expect(container.textContent ?? "").not.toContain("点击重试");
  });
});
