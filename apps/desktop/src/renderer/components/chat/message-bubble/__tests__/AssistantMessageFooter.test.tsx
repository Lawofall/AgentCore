// @vitest-environment jsdom
import { TooltipProvider } from "@/components/ui/tooltip";
import type { Message } from "@/stores/conversation";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/conversation", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/stores/conversation")>();
  return {
    ...actual,
    useConversationStore: (
      sel: (s: { currentConversationId: string | null }) => unknown,
    ) => sel({ currentConversationId: "conv-1" }),
    getActiveRuntime: () => ({ messages: [] }),
    assistantProjectionId: (m: { id: string }) => m.id,
  };
});

vi.mock("@/lib/clipboard", () => ({
  copyText: vi.fn(async () => true),
}));

vi.mock("@/lib/toast", () => ({
  notifySuccess: vi.fn(),
  notifyError: vi.fn(),
}));

vi.mock("@/hooks/useConversations", () => ({
  useDuplicateConversation: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { copyText } from "@/lib/clipboard";
import {
  AssistantMessageFooter,
  AssistantMessageMetaSummary,
  AssistantTurnInspect,
} from "../AssistantMessageFooter";

const message: Message = {
  id: "asst-1",
  role: "assistant",
  content: "",
  createdAt: "2026-08-05T00:00:00Z",
  executionId: "exec-1",
  isStreaming: false,
  traceId: "trace-1",
};

afterEach(() => {
  cleanup();
});

describe("底栏复制排查包", () => {
  it("直接露出复制排查包，点一下就复制", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect message={message} captainContext={[]} />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "更多" })).toBeNull();
    expect(screen.queryByRole("button", { name: "收到的上下文" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "复制排查包" }));
    await waitFor(() => {
      expect(copyText).toHaveBeenCalledWith(
        expect.stringContaining("trace_id: trace-1"),
      );
    });
  });

  it("有快照才露出收到的上下文", () => {
    const { rerender } = render(
      <TooltipProvider>
        <AssistantTurnInspect message={message} captainContext={[]} />
      </TooltipProvider>,
    );
    expect(screen.queryByRole("button", { name: "收到的上下文" })).toBeNull();
    rerender(
      <TooltipProvider>
        <AssistantTurnInspect
          message={message}
          captainContext={[
            {
              channel: "request",
              heading: "本轮",
              body: "你好",
              chars: 2,
              truncated: false,
              source_role: "",
              source_run_id: "",
              fidelity: "",
              files: [],
            },
          ]}
        />
      </TooltipProvider>,
    );
    expect(screen.getByRole("button", { name: "收到的上下文" })).toBeTruthy();
  });
});

function insideHoverReveal(el: HTMLElement): boolean {
  let node: HTMLElement | null = el;
  while (node) {
    if (node.className.includes("md:opacity-0")) return true;
    node = node.parentElement;
  }
  return false;
}

describe("气泡脚不挂轮次", () => {
  it("meta summary 只有费用和用时，没有 N 轮", () => {
    render(
      <AssistantMessageMetaSummary costText="¥1.00" durationMs={12_000} />,
    );
    expect(screen.getByText("¥1.00")).toBeTruthy();
    expect(screen.getByText("12s")).toBeTruthy();
    expect(screen.queryByText(/用时/)).toBeNull();
    expect(screen.queryByText(/轮/)).toBeNull();
  });

  it("用量弹出层仍展示 ReAct 轮次", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect
          message={{
            ...message,
            rounds: 3,
            usage: {
              input: 100,
              output: 50,
              reasoning: 0,
              cache_hit: 0,
              cache_miss: 0,
            },
          }}
          captainContext={[]}
        />
      </TooltipProvider>,
    );
    expect(screen.queryByText("3 轮")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "用量" }));
    expect(await screen.findByText("ReAct 轮次")).toBeTruthy();
    expect(screen.getByText("3 轮")).toBeTruthy();
  });

  it("用量弹出层把缓存和思考收在总数下，速度用 tokens/s", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect
          message={{
            ...message,
            generationMs: 2_000,
            usage: {
              input: 4_312,
              output: 1_204,
              reasoning: 628,
              cache_hit: 4_127,
              cache_miss: 185,
            },
          }}
          captainContext={[]}
        />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "用量" }));
    const panel = (await screen.findByText("输入")).parentElement!
      .parentElement!;
    expect(panel.textContent).toBe(
      "输入4,312缓存命中4,127 · 96%缓存未命中185输出1,204思考628速度602.0 tokens/s",
    );
    expect(screen.queryByText("输出速度")).toBeNull();
  });

  it("上游省略缓存拆分时只留按未命中计价", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect
          message={{
            ...message,
            generationMs: 2_000,
            usage: {
              input: 100,
              output: 80,
              reasoning: 0,
              cache_hit: 0,
              cache_miss: 0,
            },
          }}
          captainContext={[]}
        />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "用量" }));
    expect(await screen.findByText("按未命中计价")).toBeTruthy();
    expect(screen.getByText("速度")).toBeTruthy();
    expect(screen.getByText("40.0 tokens/s")).toBeTruthy();
    expect(screen.queryByText("缓存命中")).toBeNull();
    expect(screen.queryByText("缓存未命中")).toBeNull();
    expect(screen.queryByText("思考")).toBeNull();
  });

  it("用量弹出层缺 generationMs 不编速度", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect
          message={{
            ...message,
            usage: {
              input: 100,
              output: 80,
              reasoning: 0,
              cache_hit: 0,
              cache_miss: 0,
            },
          }}
          captainContext={[]}
        />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "用量" }));
    expect(await screen.findByText("输出")).toBeTruthy();
    expect(screen.queryByText("速度")).toBeNull();
  });

  it("缓存未命中为 0 时不画那一行", async () => {
    render(
      <TooltipProvider>
        <AssistantTurnInspect
          message={{
            ...message,
            usage: {
              input: 1_000,
              output: 20,
              reasoning: 0,
              cache_hit: 1_000,
              cache_miss: 0,
            },
          }}
          captainContext={[]}
        />
      </TooltipProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "用量" }));
    expect(await screen.findByText("缓存命中")).toBeTruthy();
    expect(screen.getByText("1,000 · 100%")).toBeTruthy();
    expect(screen.queryByText("缓存未命中")).toBeNull();
  });
});

describe("AssistantMessageFooter regenerate gate", () => {
  const body: Message = {
    ...message,
    content: "半成品答案",
  };

  it("showRegenerate 时露出重新生成", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <AssistantMessageFooter
            message={body}
            captainContext={[]}
            costText={null}
            onRegenerate={() => {}}
            showRegenerate
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "重新生成" })).toBeTruthy();
  });

  it("具名恢复：复制仍在，不挂重新生成", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <AssistantMessageFooter
            message={body}
            captainContext={[]}
            costText={null}
            onRegenerate={() => {}}
            showRegenerate={false}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.getByRole("button", { name: "复制" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "有帮助" })).toBeNull();
    expect(screen.queryByRole("button", { name: "没帮助" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新生成" })).toBeNull();
  });

  it("欠包时复制排查包常显，复制仍在悬停组", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <AssistantMessageFooter
            message={body}
            captainContext={[]}
            costText={null}
            onRegenerate={() => {}}
            pinSupportPack
            showRegenerate={false}
          />
        </TooltipProvider>
      </MemoryRouter>,
    );
    const pack = screen.getByRole("button", { name: "复制排查包" });
    const copy = screen.getByRole("button", { name: "复制" });
    expect(insideHoverReveal(pack)).toBe(false);
    expect(insideHoverReveal(copy)).toBe(true);
  });
});
