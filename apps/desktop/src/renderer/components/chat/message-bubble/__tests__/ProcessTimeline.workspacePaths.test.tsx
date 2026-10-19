// @vitest-environment jsdom

/**
 * 时间线正文与气泡正文同一套排版：路径不因「像文件」变成可点蓝链。
 */
import { ProcessTimeline } from "@/components/chat/message-bubble/ProcessTimeline";
import type { ProcessStep } from "@/types/events";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/stores/disclosure", () => ({
  useStreamAwareDisclosure: () => [true, vi.fn()],
  usePersistentDisclosure: () => [false, vi.fn()],
}));

afterEach(cleanup);

const emptyCards = {
  checkpoints: [] as never[],
};

function renderTimeline(process: ProcessStep[], fallbackContent: string) {
  return render(
    <ProcessTimeline
      process={process}
      isStreaming={false}
      citations={[]}
      composingTool={null}
      fallbackContent={fallbackContent}
      conversationId="c1"
      {...emptyCards}
    />,
  );
}

describe("ProcessTimeline body paths", () => {
  it("leaves a content-step path as text", () => {
    renderTimeline(
      [
        {
          kind: "content",
          text: "已写入 AgentCore/文档/工作稿/白板PRD.md。",
        },
      ],
      "",
    );
    expect(screen.queryByRole("button", { name: /打开 / })).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(document.body.textContent).toContain("白板PRD.md");
  });

  it("keeps a fallbackContent code path as code", () => {
    renderTimeline([], "见 `src/auth/login.ts`");
    expect(screen.getByText("src/auth/login.ts").tagName).toBe("CODE");
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("does not render 4-space-padded content as a text code block", () => {
    renderTimeline(
      [
        {
          kind: "content",
          text: "    工作区是空的，我会做成无需安装依赖的单页博客。",
        },
      ],
      "",
    );
    expect(document.querySelector(".code-block-lang")).toBeNull();
    expect(document.body.textContent).toContain("工作区是空的");
  });

  it("keeps a fenced content step as a code block", () => {
    renderTimeline([{ kind: "content", text: "```js\nconst x = 1\n```" }], "");
    expect(document.querySelector(".code-block-lang")?.textContent).toBe("js");
  });
});
