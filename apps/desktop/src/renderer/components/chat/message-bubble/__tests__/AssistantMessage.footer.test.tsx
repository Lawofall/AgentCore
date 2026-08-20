// @vitest-environment jsdom
/**
 * Footer 门控：按本条 isStreaming，不按会话 isGenerating。
 * 回归：长生成时已 settle 的旧气泡仍应露出重新生成/费用等操作区。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const genMock = vi.hoisted(() => ({ value: true }));
const execById = vi.hoisted(() => ({
  value: {} as Record<string, { deliveryStatus: null; plan?: unknown }>,
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    // 若误把门控写回全局 isGenerating，本 mock 恒 true 会藏 footer → 测失败。
    useActiveGenerating: () => genMock.value,
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

vi.mock("@/stores/execution", () => ({
  useExecutionStore: (
    sel: (s: {
      byId: Record<string, { deliveryStatus: null; plan?: unknown }>;
    }) => unknown,
  ) => sel({ byId: execById.value }),
}));

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: [],
      planReviews: [],
      teamPreviews: [],
    }),
  };
});

vi.mock("@/services/turns", () => ({
  runRegenerate: vi.fn(),
}));

vi.mock("@/services/turns/continuePaused", () => ({
  continuePausedTurn: vi.fn(),
}));

vi.mock("../AssistantMessageFooter", () => ({
  AssistantMessageFooter: () => <div data-testid="assistant-footer" />,
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
}));

vi.mock("@/components/chat/debate/CollapsibleSpeech", () => ({
  CollapsibleSpeech: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

import { AssistantMessage } from "../AssistantMessage";

function settledMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: "asst-1",
    role: "assistant",
    content: "已完成的旧回复",
    createdAt: "2026-08-05T00:00:00Z",
    executionId: null,
    isStreaming: false,
    ...overrides,
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

afterEach(() => {
  cleanup();
  genMock.value = true;
  execById.value = {};
});

describe("AssistantMessage footer gate", () => {
  it("本条已 settle 时，即使会话仍 generating 也显示 footer", () => {
    genMock.value = true;
    renderBubble(settledMessage());
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("本条仍在 streaming 时不显示 footer", () => {
    genMock.value = false;
    renderBubble(settledMessage({ isStreaming: true, content: "流式中…" }));
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("空正文且非失败时不显示 footer", () => {
    renderBubble(settledMessage({ content: "" }));
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("空正文 + message.error 时显示 footer", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    );
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("错误卡不挂重新生成（定案 A；底栏 footer 另测）", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    );
    const errText = screen.getByText("模型调用失败，请重试。");
    const errCard = errText.closest("div");
    expect(errCard).toBeTruthy();
    expect(
      within(errCard as HTMLElement).queryByRole("button", {
        name: "重新生成",
      }),
    ).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
    // 本测 mock 了 Footer；只断言错误卡已摘按钮，footer 仍挂载。
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("空正文 + runs.error 时显示 footer", () => {
    renderBubble(
      settledMessage({
        content: "",
        // Duck-typed journal error only (no message.error / non-synthesizable finish).
        runs: {
          events: [],
          finishReason: "stop",
          error: { code: "LLM_ERROR", message: "上游超时" },
        } as Message["runs"],
      }),
    );
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("空正文 + 可合成空失败（finishReason=error）时显示 footer", () => {
    renderBubble(settledMessage({ content: "", finishReason: "error" }));
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("空正文 + cancelled 不占聊天面；interrupted 不在气泡画失败卡（P1）", () => {
    renderBubble(settledMessage({ content: "", finishReason: "cancelled" }));
    expect(screen.queryByTestId("assistant-stopped-notice")).toBeNull();
    expect(screen.queryByText("已停止")).toBeNull();
    // No footer on cancelled-alone (timeline omits the stop face).
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    cleanup();
    // Interrupted: unique verdict is the composer hint — bubble has no red card / footer.
    renderBubble(settledMessage({ content: "", finishReason: "interrupted" }));
    expect(screen.queryByText(/已中断/)).toBeNull();
    expect(screen.queryByText(/直接发送下一条/)).toBeNull();
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
  });

  it("attested paused：只已暂停+继续，不亮限流横幅或中断 hint", () => {
    renderBubble(
      settledMessage({
        content: "",
        finishReason: "paused",
        outcome: "paused",
        error: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请约 2 秒后再试。",
        },
      }),
    );
    expect(screen.getByTestId("paused-continue-surface")).toBeTruthy();
    expect(screen.getByText("已暂停")).toBeTruthy();
    expect(screen.getByText("上游限流，暂时无法继续本回合。")).toBeTruthy();
    expect(screen.queryByText(/请约/)).toBeNull();
    expect(screen.queryByText(/稍后再试/)).toBeNull();
    expect(screen.getByRole("button", { name: "继续" })).toBeTruthy();
    expect(screen.queryByText(/直接发送下一条/)).toBeNull();
    expect(screen.queryByText("已中断")).toBeNull();
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("限流 + interrupted finish：只亮限流横幅，不并写「直接发送下一条」", () => {
    renderBubble(
      settledMessage({
        content: "",
        finishReason: "interrupted",
        error: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请约 2 秒后再试。",
        },
      }),
    );
    expect(screen.getByText(/上游限流/)).toBeTruthy();
    expect(screen.queryByText(/直接发送下一条/)).toBeNull();
    expect(screen.queryByText("已中断")).toBeNull();
  });

  it("partial + 限流：气泡不重复红卡（判决在输入区）", () => {
    renderBubble(
      settledMessage({
        content: "",
        outcome: "partial",
        finishReason: "error",
        error: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请稍后再试。",
        },
      }),
    );
    expect(screen.queryByText(/上游限流/)).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });
});

describe("AssistantMessage empty-response single surface", () => {
  it("LLM_EMPTY_RESPONSE：只错误卡，不叠 FinishReasonChip / 连通升级句", () => {
    renderBubble(
      settledMessage({
        id: "empty-1",
        content: "",
        finishReason: "degraded",
        error: {
          code: "LLM_EMPTY_RESPONSE",
          message: "模型多次空响应 · 模型返回空内容",
          context: { empty_diagnosis: "silent_empty" },
        },
      }),
    );
    expect(screen.getByText(/模型多次空响应/)).toBeTruthy();
    expect(screen.queryByText("空响应收尾")).toBeNull();
    expect(screen.queryByText(/降级完成/)).toBeNull();
    expect(screen.queryByText(/模型返回空内容/)).toBeTruthy();
    // Chip would show diagnosis alone; error card already has it — no separate chip row.
    expect(screen.queryByText("Base URL")).toBeNull();
    expect(screen.queryByText(/设置 · 服务商/)).toBeNull();
  });

  it("legacy oauth_expired diagnosis：错误卡唯一面，无 Sub2API / 降级完成", () => {
    renderBubble(
      settledMessage({
        id: "empty-oauth",
        content: "",
        finishReason: "degraded",
        error: {
          code: "LLM_EMPTY_RESPONSE",
          message:
            "模型多次空响应 · 上游返回了网页或登录页，请检查服务商地址与鉴权",
          context: { empty_diagnosis: "oauth_expired" },
        },
      }),
    );
    expect(screen.getByText(/上游返回了网页或登录页/)).toBeTruthy();
    expect(screen.queryByText(/Sub2API/)).toBeNull();
    expect(screen.queryByText(/降级完成/)).toBeNull();
    expect(screen.queryByText("空响应收尾")).toBeNull();
  });

  it("degraded 无空响应错误时仍可显示 FinishReasonChip", () => {
    renderBubble(
      settledMessage({
        content: "部分输出",
        finishReason: "degraded",
      }),
    );
    expect(screen.getByText("空响应收尾")).toBeTruthy();
    expect(screen.queryByText(/降级完成/)).toBeNull();
  });

  it("空正文 + finishReason=degraded 无 error 载荷：仍合成非空脸", () => {
    renderBubble(
      settledMessage({
        content: "",
        finishReason: "degraded",
      }),
    );
    expect(screen.getByText("模型返回空内容，请重试。")).toBeTruthy();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("空正文 + usage.error（刷新 REST 路径）有脸", () => {
    renderBubble(
      settledMessage({
        content: "",
        finishReason: "error",
        usage: {
          input: 0,
          output: 0,
          reasoning: 0,
          cache_hit: 0,
          cache_miss: 0,
          error: {
            code: "LLM_INSUFFICIENT_BALANCE",
            message: "上游账户余额不足，请充值或更换 Key。",
          },
        },
      }),
    );
    expect(screen.getByText(/上游账户余额不足/)).toBeTruthy();
  });

  it("有正文 + finishReason=error + 无 message.error：错误卡不静默，无灰标调用失败", () => {
    renderBubble(
      settledMessage({
        id: "hard-body-1",
        content: "部分已生成正文",
        finishReason: "error",
        runs: {
          events: [],
          finishReason: "error",
          error: {
            code: "LLM_KEY_INVALID",
            message:
              "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
          },
        } as Message["runs"],
      }),
    );
    expect(screen.getByText(/平台模型暂时不可用/)).toBeTruthy();
    expect(screen.getByText("部分已生成正文")).toBeTruthy();
    // Chip must not stack on the error card.
    expect(screen.queryByText("调用失败")).toBeNull();
  });

  it("空正文硬失败：只错误卡，不叠灰标调用失败", () => {
    renderBubble(
      settledMessage({
        id: "hard-empty-1",
        content: "",
        finishReason: "error",
        error: {
          code: "LLM_KEY_INVALID",
          message:
            "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
        },
      }),
    );
    expect(screen.getByText(/平台模型暂时不可用/)).toBeTruthy();
    expect(screen.queryByText("调用失败")).toBeNull();
  });
});

describe("AssistantMessage error card chrome", () => {
  it("限流 / 无 action：错误卡灰底，复制排查包不走红", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请稍后再试。",
        },
      }),
    );
    const errCard = screen
      .getByText("上游限流，暂时无法继续本回合。请稍后再试。")
      .closest("div");
    expect(errCard?.className).toContain("bg-muted/40");
    expect(errCard?.className).not.toContain("bg-primary/10");
    const copyBtn = screen.getByRole("button", { name: "复制排查包" });
    expect(copyBtn.className).toContain("text-muted-foreground");
    expect(copyBtn.className).not.toContain("destructive");
    expect(screen.queryByRole("button", { name: "去服务商" })).toBeNull();
  });

  it("有去配置：错误卡蓝底，动作钮 primary，复制排查包跟蓝档", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: {
          code: "LLM_KEY_REQUIRED",
          message: "请先接入自己的 API Key，再发起对话。",
        },
      }),
    );
    const errCard = screen
      .getByText("请先接入自己的 API Key，再发起对话。")
      .closest("div");
    expect(errCard?.className).toContain("bg-primary/10");
    expect(errCard?.className).not.toContain("bg-muted/40");
    const actionBtn = screen.getByRole("button", { name: "去服务商" });
    expect(actionBtn.className).toContain("bg-primary");
    expect(actionBtn.className).not.toContain("bg-destructive");
    const copyBtn = screen.getByRole("button", { name: "复制排查包" });
    expect(copyBtn.className).toContain("text-primary/70");
    expect(copyBtn.className).not.toContain("destructive");
  });

  it("credential_source=platform：接入自己的 Key，不是去服务商", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: {
          code: "LLM_KEY_INVALID",
          message:
            "平台模型暂时不可用（上游鉴权失败）。请改用自己的 API Key，或联系管理员。",
          context: { credential_source: "platform" },
        },
      }),
    );
    expect(screen.getByRole("button", { name: "接入自己的 Key" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "去服务商" })).toBeNull();
  });

  it("credential_source=user：去服务商", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: {
          code: "LLM_KEY_INVALID",
          message: "API Key 无效或已过期，请检查后重试。",
          context: { credential_source: "user" },
        },
      }),
    );
    expect(screen.getByRole("button", { name: "去服务商" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "接入自己的 Key" })).toBeNull();
  });
});

describe("AssistantMessage trusts turnOutcome flags", () => {
  it("有正文的中断/停止：半成品正文就是结果，不另画失败卡", () => {
    renderBubble(
      settledMessage({ content: "半成品答案", finishReason: "cancelled" }),
    );
    expect(screen.getByText("半成品答案")).toBeTruthy();
    expect(screen.queryByText("已停止")).toBeNull();
    cleanup();
    renderBubble(
      settledMessage({
        content: "半成品答案",
        finishReason: "cancelled",
        turnWarning: "已停止",
      }),
    );
    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
    cleanup();
    renderBubble(
      settledMessage({ content: "半成品答案", finishReason: "interrupted" }),
    );
    expect(screen.getByText("半成品答案")).toBeTruthy();
    expect(screen.queryByText(/已中断/)).toBeNull();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("有团队图时条是主判决，气泡不重复红卡", () => {
    execById.value = {
      "asst-1": { deliveryStatus: null, plan: { agents: [] } },
    };
    renderBubble(
      settledMessage({
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    );
    expect(screen.queryByText("模型调用失败，请重试。")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
    // recovery.none on error: footer 重新生成 is the unique retry control.
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });
});
