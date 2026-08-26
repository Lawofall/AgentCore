// @vitest-environment jsdom

import { Markdown } from "@/components/chat/Markdown";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

afterEach(cleanup);

describe("Markdown workspace path links", () => {
  it("turns a prose path into a button that opens the file", () => {
    const onOpen = vi.fn();
    render(
      <Markdown
        content="已写入 AgentCore/文档/工作稿/白板PRD.md。"
        onOpenWorkspacePath={onOpen}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", {
        name: "打开 AgentCore/文档/工作稿/白板PRD.md",
      }),
    );
    expect(onOpen).toHaveBeenCalledWith("AgentCore/文档/工作稿/白板PRD.md");
  });

  it("turns inline-code paths into the same opener", () => {
    const onOpen = vi.fn();
    render(
      <Markdown
        content="见 `src/auth/login.ts`"
        onOpenWorkspacePath={onOpen}
      />,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "打开 src/auth/login.ts" }),
    );
    expect(onOpen).toHaveBeenCalledWith("src/auth/login.ts");
  });

  it("does not link paths when the opener is omitted", () => {
    render(<Markdown content="已写入 AgentCore/文档/工作稿/白板PRD.md。" />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("leaves fence contents untouched", () => {
    const onOpen = vi.fn();
    render(
      <Markdown
        content={"```\nAgentCore/文档/工作稿/白板PRD.md\n```"}
        onOpenWorkspacePath={onOpen}
      />,
    );
    expect(screen.queryByRole("button", { name: /打开 / })).toBeNull();
  });
});
