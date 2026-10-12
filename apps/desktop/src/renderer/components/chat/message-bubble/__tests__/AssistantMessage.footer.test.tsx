// @vitest-environment jsdom
/**
 * Footer 门控：按本条 isStreaming，外加「末条助手 + 本轮还在写」。
 * 回归：长生成时已 settle 的旧气泡仍应露出重新生成/费用等操作区。
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

const genMock = vi.hoisted(() => ({ value: true }));
const convSlice = vi.hoisted(() => ({
  currentConversationId: "conv-1" as string | null,
  byId: undefined as
    | Record<
        string,
        {
          isGenerating: boolean;
          turnPhase: string;
          messages: { id: string; role: string }[];
        }
      >
    | undefined,
}));
const execById = vi.hoisted(() => ({
  value: {} as Record<string, { deliveryStatus: null; plan?: unknown }>,
}));
const interactionCards = vi.hoisted(() => ({
  checkpoints: [] as { status: "pending" | "resolved"; question?: string }[],
}));

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    // 若误把门控写回全局 isGenerating，本 mock 恒 true 会藏 footer → 测失败。
    useActiveGenerating: () => genMock.value,
    useConversationStore: (
      sel: (s: {
        currentConversationId: string | null;
        byId?: typeof convSlice.byId;
      }) => unknown,
    ) =>
      sel({
        currentConversationId: convSlice.currentConversationId,
        byId: convSlice.byId,
      }),
    useLiveTailWriting: (messageId: string) => {
      const id = convSlice.currentConversationId;
      if (!id) return false;
      const rt = convSlice.byId?.[id];
      if (!rt) return false;
      const writing =
        rt.isGenerating ||
        rt.turnPhase === "preflight" ||
        rt.turnPhase === "streaming" ||
        rt.turnPhase === "stopping";
      if (!writing) return false;
      for (let i = rt.messages.length - 1; i >= 0; i--) {
        if (rt.messages[i].role === "assistant") {
          return rt.messages[i].id === messageId;
        }
      }
      return false;
    },
    getActiveRuntime: () => {
      const id = convSlice.currentConversationId;
      const slice = id ? convSlice.byId?.[id] : undefined;
      return {
        messages: slice?.messages ?? [],
        isGenerating: slice?.isGenerating ?? false,
        turnPhase: slice?.turnPhase ?? "idle",
      };
    },
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
      sel: (s: {
        byId: Record<string, { deliveryStatus: null; plan?: unknown }>;
      }) => unknown,
    ) => sel({ byId: execById.value }),
    useMessageExecution: () => null,
  };
});

vi.mock("@/stores/interactions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/interactions")>();
  return {
    ...actual,
    useMessageInteractionCards: () => ({
      checkpoints: interactionCards.checkpoints,
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
  AssistantMessageMetaSummary: () => null,
  AssistantTurnInspect: () => (
    <button type="button" aria-label="复制排查包">
      复制排查包
    </button>
  ),
}));

vi.mock("@/components/chat/Markdown", () => ({
  Markdown: ({ content }: { content: string }) => <div>{content}</div>,
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
  convSlice.currentConversationId = "conv-1";
  convSlice.byId = undefined;
  execById.value = {};
  interactionCards.checkpoints = [];
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
    expect(screen.queryByRole("button", { name: "复制" })).toBeNull();
    expect(screen.queryByRole("button", { name: "有帮助" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "复制排查包" })).toBeNull();
  });

  it("末条助手气泡已标 settle，但本轮 turnPhase 仍在写：不显示 footer", () => {
    convSlice.byId = {
      "conv-1": {
        isGenerating: false,
        turnPhase: "streaming",
        messages: [{ id: "asst-1", role: "assistant" }],
      },
    };
    renderBubble(settledMessage({ content: "团队 4/4 收工，我接着核一下…" }));
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
    expect(screen.queryByRole("button", { name: "复制" })).toBeNull();
    expect(screen.queryByRole("button", { name: "有帮助" })).toBeNull();
  });

  it("更早的已 settle 气泡在本轮仍写时照样露出 footer", () => {
    convSlice.byId = {
      "conv-1": {
        isGenerating: false,
        turnPhase: "streaming",
        messages: [
          { id: "asst-old", role: "assistant" },
          { id: "asst-1", role: "assistant" },
        ],
      },
    };
    renderBubble(settledMessage({ id: "asst-old", content: "上一句已经说完" }));
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("空正文且非失败时不显示 footer", () => {
    renderBubble(settledMessage({ content: "" }));
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("空正文 + message.error 不占气泡", () => {
    const { container } = renderBubble(
      settledMessage({
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("空失败不在气泡挂重新生成", () => {
    renderBubble(
      settledMessage({
        content: "",
        error: { code: "LLM_ERROR", message: "模型调用失败，请重试。" },
      }),
    );
    expect(screen.queryByText("模型调用失败，请重试。")).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试" })).toBeNull();
  });

  it("空正文 + runs.error 不占气泡", () => {
    const { container } = renderBubble(
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
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
  });

  it("空正文 + 可合成空失败（finishReason=error）不占气泡", () => {
    const { container } = renderBubble(
      settledMessage({ content: "", finishReason: "error" }),
    );
    expect(container.textContent).toBe("");
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
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

  it("attested paused 有正文：已暂停+继续，仍挂效用 footer", () => {
    renderBubble(
      settledMessage({
        content: "半成品答案",
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
    expect(screen.getByRole("button", { name: "继续" })).toBeTruthy();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("限流 + interrupted finish：句子不进气泡", () => {
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
    expect(screen.queryByText(/上游限流/)).toBeNull();
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
  it("LLM_EMPTY_RESPONSE：空壳不进气泡，不叠软收尾灰标 / 连通升级句", () => {
    renderBubble(
      settledMessage({
        id: "empty-1",
        content: "",
        finishReason: "degraded",
        error: {
          code: "LLM_EMPTY_RESPONSE",
          message: "模型空响应 · 模型返回空内容",
          context: { empty_diagnosis: "silent_empty" },
        },
      }),
    );
    expect(screen.queryByText(/模型空响应/)).toBeNull();
    expect(screen.queryByText("空响应收尾")).toBeNull();
    expect(screen.queryByText(/降级完成/)).toBeNull();
    expect(screen.queryByText(/模型返回空内容/)).toBeNull();
    // Chip would show diagnosis alone; error card already has it — no separate chip row.
    expect(screen.queryByText("Base URL")).toBeNull();
    expect(screen.queryByText(/设置 · 服务商/)).toBeNull();
  });

  it("legacy oauth_expired diagnosis：空壳不进气泡，无 Sub2API / 降级完成", () => {
    renderBubble(
      settledMessage({
        id: "empty-oauth",
        content: "",
        finishReason: "degraded",
        error: {
          code: "LLM_EMPTY_RESPONSE",
          message:
            "模型空响应 · 上游返回了网页或登录页，请检查服务商地址与鉴权",
          context: { empty_diagnosis: "oauth_expired" },
        },
      }),
    );
    expect(screen.queryByText(/上游返回了网页或登录页/)).toBeNull();
    expect(screen.queryByText(/Sub2API/)).toBeNull();
    expect(screen.queryByText(/降级完成/)).toBeNull();
    expect(screen.queryByText("空响应收尾")).toBeNull();
  });

  it("degraded 有正文时不贴软收尾灰标", () => {
    renderBubble(
      settledMessage({
        content: "部分输出",
        finishReason: "degraded",
      }),
    );
    expect(screen.queryByText("空响应收尾")).toBeNull();
    expect(screen.queryByText(/降级完成/)).toBeNull();
    expect(screen.getByText("部分输出")).toBeTruthy();
  });

  it("空正文 + finishReason=degraded 无 error 载荷：仍合成非空脸", () => {
    renderBubble(
      settledMessage({
        content: "",
        finishReason: "degraded",
      }),
    );
    expect(screen.queryByText("模型返回空内容，请重试。")).toBeNull();
    expect(screen.queryByTestId("assistant-footer")).toBeNull();
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
    expect(screen.queryByText(/上游账户余额不足/)).toBeNull();
  });

  it("有正文 + finishReason=error：句子不进气泡，无灰标调用失败", () => {
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
    expect(screen.queryByText(/平台模型暂时不可用/)).toBeNull();
    expect(screen.getByText("部分已生成正文")).toBeTruthy();
    // Chip must not stack on the error card.
    expect(screen.queryByText("调用失败")).toBeNull();
  });

  it("空正文硬失败：不占气泡，不叠灰标调用失败", () => {
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
    expect(screen.queryByText(/平台模型暂时不可用/)).toBeNull();
    expect(screen.queryByText("调用失败")).toBeNull();
  });
});

describe("AssistantMessage leaves failure copy off the bubble", () => {
  it("empty rate-limit does not render a card", () => {
    const { container } = renderBubble(
      settledMessage({
        content: "",
        error: {
          code: "LLM_RATE_LIMIT",
          message: "上游限流，暂时无法继续本回合。请稍后再试。",
        },
      }),
    );
    expect(container.textContent).toBe("");
  });
});

describe("AssistantMessage trusts turnOutcome flags", () => {
  it("有正文的中断/停止：半成品正文就是结果，不另画失败卡", () => {
    renderBubble(
      settledMessage({ content: "半成品答案", finishReason: "cancelled" }),
    );
    expect(screen.getByText("半成品答案")).toBeTruthy();
    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByTestId("turn-warning-banner")).toBeNull();
    cleanup();
    renderBubble(
      settledMessage({
        content: "半成品答案",
        finishReason: "cancelled",
        turnWarning: "已停止",
      }),
    );
    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByTestId("turn-warning-banner")).toBeNull();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
    cleanup();
    renderBubble(
      settledMessage({
        content: "半成品答案",
        finishReason: "cancelled",
        turnWarning: "当前模型可能不支持工具调用，复杂任务效果可能受限。",
      }),
    );
    expect(screen.queryByTestId("turn-warning-banner")).toBeNull();
    expect(
      screen.queryByText("当前模型可能不支持工具调用，复杂任务效果可能受限。"),
    ).toBeNull();
    expect(screen.queryByText("已停止")).toBeNull();
    cleanup();
    renderBubble(
      settledMessage({ content: "半成品答案", finishReason: "interrupted" }),
    );
    expect(screen.getByText("半成品答案")).toBeTruthy();
    expect(screen.queryByText(/已中断/)).toBeNull();
    expect(screen.getByTestId("assistant-footer")).toBeTruthy();
  });

  it("非停止回合仍画预检 turn_warning 横幅", () => {
    renderBubble(
      settledMessage({
        content: "正文",
        finishReason: "end_turn",
        turnWarning: "当前模型可能不支持工具调用，复杂任务效果可能受限。",
      }),
    );
    expect(screen.getByTestId("turn-warning-banner")).toBeTruthy();
    expect(
      screen.getByText("当前模型可能不支持工具调用，复杂任务效果可能受限。"),
    ).toBeTruthy();
  });

  it("已结算 ask 卡 + user-stop：不把「已停止」画成气泡警告条", () => {
    interactionCards.checkpoints = [{ status: "resolved", question: "" }];
    renderBubble(
      settledMessage({
        content: "好，按确认的方案开工",
        finishReason: "cancelled",
      }),
    );
    expect(screen.getByText("好，按确认的方案开工")).toBeTruthy();
    expect(screen.queryByText("已停止")).toBeNull();
    expect(screen.queryByTestId("turn-warning-banner")).toBeNull();
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
