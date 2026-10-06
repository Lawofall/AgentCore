// @vitest-environment jsdom
import type { Message } from "@/stores/conversation";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/conversations", () => ({
  previewContextCut: vi.fn(),
  commitContextCut: vi.fn(),
}));

import { commitContextCut, previewContextCut } from "@/services/conversations";
import { ContextCutAction } from "../ContextCutAction";

function msg(id: string, role: "user" | "assistant", minute: number): Message {
  return {
    id,
    role,
    content: id,
    createdAt: `2026-10-04T00:0${minute}:00Z`,
    executionId: null,
    isStreaming: false,
  };
}

const u1 = msg("u1", "user", 1);
const a1 = msg("a1", "assistant", 2);
const u2 = msg("u2", "user", 3);
const a2 = msg("a2", "assistant", 4);

const SUMMARY = [
  "## 已确立",
  "",
  "- `*a*` 与 `_a_`",
  "",
  "![图](https://evil.example/a.png)",
  "",
  "![x](javascript:alert(1))",
  "",
  "```ts",
  "const n = 1",
  "```",
].join("\n");

function seed() {
  useConversationStore.setState({
    currentConversationId: "conv-1",
    byId: {
      "conv-1": {
        ...EMPTY_RUNTIME,
        messages: [u1, a1, u2, a2],
      },
    },
  });
}

afterEach(() => {
  cleanup();
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  });
  vi.clearAllMocks();
});

describe("按这条继续", () => {
  it("把切点写成一句，确认时提交改过的摘要源码", async () => {
    vi.mocked(previewContextCut).mockResolvedValue({
      summary: SUMMARY,
      keepMessageId: "u2",
      foldThrough: a1.createdAt,
      foldDigest: "ab".repeat(32),
      foldedCount: 2,
    });
    vi.mocked(commitContextCut).mockResolvedValue({ id: "conv-1" } as never);
    seed();
    render(<ContextCutAction message={a2} />);
    fireEvent.click(screen.getByRole("button", { name: "从此按这条继续" }));

    const decision =
      "收起更早的 2 条，换成下面这份摘要，引出这条回复的那句话也留下，屏幕上仍可上翻。";
    expect(await screen.findByText(decision)).toBeTruthy();
    const dialog = screen.getByRole("dialog").textContent ?? "";
    expect(dialog).not.toContain("这条和之后的原文继续发给模型");
    expect(dialog).not.toContain("引出这条回复的那句话也会原样留下");

    const editor = screen.getByRole("textbox", { name: "摘要" });
    expect((editor as HTMLTextAreaElement).value).toContain("## 已确立");
    expect((editor as HTMLTextAreaElement).value).toContain("`*a*`");
    fireEvent.change(editor, { target: { value: "改过的方案" } });
    fireEvent.click(screen.getByRole("button", { name: "就按这个继续" }));
    await waitFor(() => {
      expect(commitContextCut).toHaveBeenCalledWith(
        "conv-1",
        "a2",
        "ab".repeat(32),
        "改过的方案",
      );
    });
  });

  it("摘要空着时不能确认", async () => {
    vi.mocked(previewContextCut).mockResolvedValue({
      summary: "一句摘要",
      keepMessageId: "u2",
      foldThrough: a1.createdAt,
      foldDigest: "ab".repeat(32),
      foldedCount: 2,
    });
    vi.mocked(commitContextCut).mockResolvedValue({ id: "conv-1" } as never);
    seed();
    render(<ContextCutAction message={u2} />);
    fireEvent.click(screen.getByRole("button", { name: "从此按这条继续" }));
    const editor = await screen.findByRole("textbox", { name: "摘要" });
    fireEvent.change(editor, { target: { value: "   " } });
    expect(screen.getByRole("button", { name: "就按这个继续" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(commitContextCut).not.toHaveBeenCalled();
  });

  it("切在用户句上时不另提那句话", async () => {
    vi.mocked(previewContextCut).mockResolvedValue({
      summary: "一句摘要",
      keepMessageId: "u2",
      foldThrough: a1.createdAt,
      foldDigest: "ab".repeat(32),
      foldedCount: 2,
    });
    seed();
    render(<ContextCutAction message={u2} />);
    fireEvent.click(screen.getByRole("button", { name: "从此按这条继续" }));

    const decision = "收起更早的 2 条，换成下面这份摘要，屏幕上仍可上翻。";
    expect(await screen.findByText(decision)).toBeTruthy();
    expect(screen.getByRole("dialog").textContent ?? "").not.toContain(
      "引出这条回复的那句话",
    );
    expect(
      (screen.getByRole("textbox", { name: "摘要" }) as HTMLTextAreaElement)
        .value,
    ).toBe("一句摘要");
  });
});
