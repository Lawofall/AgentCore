// @vitest-environment jsdom
/**
 * ResumePrompt only surfaces cold pending cards — no frameless
 *「已授权 · 执行中断 / 一键继续」DecisionCard (abolished).
 */

import { cleanup, render } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumePrompt } from "../ResumePrompt";

vi.mock("@/services/interactionSubmit", () => ({
  submitInteraction: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: "c1" }),
}));

vi.mock("@/stores/pausedTurns", () => ({
  usePausedTurnStore: (sel: (s: { pending: unknown[] }) => unknown) =>
    sel({ pending: [] }),
}));

vi.mock("@/stores/interactions", async () => {
  const actual = await vi.importActual<typeof import("@/stores/interactions")>(
    "@/stores/interactions",
  );
  return {
    ...actual,
    useInteractionStore: (
      sel: (s: { byId: Map<string, unknown> }) => unknown,
    ) => sel({ byId: new Map() }),
  };
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

beforeEach(() => {});

describe("ResumePrompt · no frameless continue card", () => {
  it("renders nothing when only interrupted (no cold pending) — no DecisionCard", () => {
    const { container } = render(<ResumePrompt />);
    expect(container.firstChild).toBeNull();
  });

  it("does not surface 已授权 · 执行中断 / 一键继续 copy", () => {
    render(<ResumePrompt />);
    expect(document.body.textContent).not.toContain("已授权 · 执行中断");
    expect(document.body.textContent).not.toContain("一键继续");
  });
});
