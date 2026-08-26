// @vitest-environment jsdom
/**
 * 终稿路径可点必须接到时间线：多 Agent / 有 process 的回合不走 AssistantMessage
 * 的无 process Markdown 分支。
 */
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import type { ProcessStep } from "@/types/events";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

afterEach(cleanup);

const emptyCards = {
  checkpoints: [] as never[],
  planReviews: [] as never[],
};

function renderTimeline(
  process: ProcessStep[],
  fallbackContent: string,
  onOpen: (path: string) => void,
) {
  return render(
    <ProcessTimeline
      process={process}
      isStreaming={false}
      citations={[]}
      composingTool={null}
      fallbackContent={fallbackContent}
      conversationId="c1"
      onOpenWorkspacePath={onOpen}
      {...emptyCards}
    />,
  );
}

describe("ProcessTimeline workspace paths", () => {
  it("opens a content-step path when the opener is wired", () => {
    const onOpen = vi.fn();
    renderTimeline(
      [
        {
          kind: "content",
          text: "已写入 AgentCore/文档/工作稿/白板PRD.md。",
        },
      ],
      "",
      onOpen,
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "打开 AgentCore/文档/工作稿/白板PRD.md",
      }),
    );
    expect(onOpen).toHaveBeenCalledWith("AgentCore/文档/工作稿/白板PRD.md");
  });

  it("opens a fallbackContent path when there is no content step", () => {
    const onOpen = vi.fn();
    renderTimeline([], "见 `src/auth/login.ts`", onOpen);
    fireEvent.click(
      screen.getByRole("button", { name: "打开 src/auth/login.ts" }),
    );
    expect(onOpen).toHaveBeenCalledWith("src/auth/login.ts");
  });
});
