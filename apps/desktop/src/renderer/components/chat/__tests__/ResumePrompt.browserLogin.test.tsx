// @vitest-environment jsdom
/**
 * ResumePrompt · ask_user browser_login：
 * - pending + browserLogin → 「需要你登录」；不自动 showBrowser，点「打开浏览器」才揭示
 * - 「已登录，继续」走 cold ask_user continue
 */
import { TooltipProvider } from "@/components/ui/tooltip";
import type { PendingResume } from "@/stores/pausedTurns";
import { usePausedTurnStore } from "@/stores/pausedTurns";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumePrompt } from "../ResumePrompt";

const showBrowser = vi.fn();
const submitInteraction = vi.fn();

vi.mock("@/stores/sidePanel", () => ({
  useSidePanelStore: {
    getState: () => ({ showBrowser }),
  },
}));

vi.mock("@/services/interactionSubmit", () => ({
  submitInteraction: (...args: unknown[]) => submitInteraction(...args),
  notifySubmitInteractionResult: vi.fn(),
  submitInteractionFeedback: (r: string) => r,
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: "c1" }),
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

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

const loginTurn: PendingResume = {
  messageId: "m1",
  conversationId: "c1",
  checkpointId: "cp-login",
  kind: "ask_user",
  userMessage: "打开后台",
  userMessageId: "u1",
  steps: [],
  pending: [],
  question: "请在右坞完成登录",
  questions: [],
  intent: "decision",
  browserLogin: true,
  origin: "server",
};

afterEach(() => {
  cleanup();
  usePausedTurnStore.setState({ pending: [] });
});

beforeEach(() => {
  showBrowser.mockClear();
  submitInteraction.mockReset();
  submitInteraction.mockResolvedValue("ok");
  usePausedTurnStore.setState({ pending: [loginTurn] });
});

describe("ResumePrompt · ask_user browser_login", () => {
  it("renders login card without auto-reveal; continues on CTA", async () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ResumePrompt />
        </TooltipProvider>
      </MemoryRouter>,
    );

    expect(screen.getByText(/需要你登录/)).toBeTruthy();
    expect(screen.getByText("请在右坞完成登录")).toBeTruthy();
    expect(showBrowser).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "打开浏览器" }));
    expect(showBrowser).toHaveBeenCalledTimes(1);

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "已登录，继续" }));
    });

    expect(submitInteraction).toHaveBeenCalledWith(
      expect.objectContaining({
        id: "cp-login",
        kind: "ask_user",
        cold: expect.objectContaining({
          decision: "continue",
          note: "已登录，继续",
        }),
      }),
    );
  });

  it("does not offer 按假设继续 on the login card", () => {
    render(
      <MemoryRouter>
        <TooltipProvider>
          <ResumePrompt />
        </TooltipProvider>
      </MemoryRouter>,
    );
    expect(screen.queryByRole("button", { name: "按假设继续" })).toBeNull();
    expect(screen.getByRole("button", { name: "已登录，继续" })).toBeTruthy();
  });
});
