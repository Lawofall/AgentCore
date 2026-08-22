// @vitest-environment jsdom
/**
 * leftover team_preview: no clickable kickoff / unstick shell.
 * fold / InteractionKind still recognize the kind; ResumePrompt does not paint it.
 */

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ResumePrompt } from "../ResumePrompt";

const submitInteraction = vi.fn().mockResolvedValue("ok");

vi.mock("@/services/interactionSubmit", () => ({
  submitInteraction: (...args: unknown[]) => submitInteraction(...args),
  notifySubmitInteractionResult: vi.fn(),
}));

vi.mock("@/lib/toast", () => ({
  notifyError: vi.fn(),
}));

const pendingRef: { current: unknown[] } = { current: [] };
const interactionById = new Map<
  string,
  { kind?: string; payload?: Record<string, unknown>; status?: string }
>();

vi.mock("@/stores/conversation", () => ({
  useConversationStore: (
    sel: (s: { currentConversationId: string }) => unknown,
  ) => sel({ currentConversationId: "c1" }),
}));

vi.mock("@/stores/pausedTurns", () => ({
  usePausedTurnStore: (sel: (s: { pending: unknown[] }) => unknown) =>
    sel({ pending: pendingRef.current }),
}));

vi.mock("@/stores/interactions", async () => {
  const actual = await vi.importActual<typeof import("@/stores/interactions")>(
    "@/stores/interactions",
  );
  return {
    ...actual,
    useInteractionStore: (
      sel: (s: {
        byId: Map<
          string,
          { kind?: string; payload?: Record<string, unknown>; status?: string }
        >;
      }) => unknown,
    ) => sel({ byId: interactionById }),
  };
});

function makeTeamPreview(over: Record<string, unknown> = {}) {
  return {
    messageId: "m1",
    conversationId: "c1",
    checkpointId: "cp1",
    kind: "team_preview",
    userMessage: "组团做定价",
    userMessageId: "u1",
    steps: [],
    pending: [],
    workers: [
      {
        run_id: "r1",
        role: "研究员",
        task: "调研",
        depends_on: [],
      },
    ],
    tools: ["file_write", "code_execute"],
    primitive: "delegate",
    motion: "",
    form: "",
    sides: [],
    maxRounds: 0,
    thorough: true,
    question: "",
    assumptions: [],
    questions: [],
    intent: "kickoff",
    origin: "server",
    ...over,
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  pendingRef.current = [];
  interactionById.clear();
});

beforeEach(() => {
  pendingRef.current = [makeTeamPreview()];
  submitInteraction.mockReset();
  submitInteraction.mockResolvedValue("ok");
});

function expectNoKickoffShell(): void {
  expect(screen.queryByText("此回合还停在开工确认")).toBeNull();
  expect(screen.queryByRole("button", { name: "取消" })).toBeNull();
  expect(screen.queryByRole("button", { name: "继续" })).toBeNull();
  expect(screen.queryByRole("button", { name: "授权并开工" })).toBeNull();
  expect(screen.queryByRole("button", { name: "授权开赛" })).toBeNull();
  expect(screen.queryByRole("button", { name: "开做" })).toBeNull();
  expect(screen.queryByRole("button", { name: "调整" })).toBeNull();
}

describe("ResumePrompt · leftover team_preview 不画可点开工壳", () => {
  it("存量 pending 不画继续 / 取消，也不画编制表或授权开工", () => {
    const { container } = render(<ResumePrompt />);
    expect(container.querySelector(".mx-4")).toBeNull();
    expectNoKickoffShell();
    expect(submitInteraction).not.toHaveBeenCalled();
  });

  it("辩论存量同样不画可点壳", () => {
    pendingRef.current = [
      makeTeamPreview({
        primitive: "debate",
        tools: [],
        workers: [],
        motion: "该不该涨价",
        sides: [
          { side_id: "pro", label: "正方", stance: "该" },
          { side_id: "con", label: "反方", stance: "不该" },
        ],
        maxRounds: 2,
      }),
    ];
    render(<ResumePrompt />);
    expectNoKickoffShell();
    expect(screen.queryByText("该不该涨价")).toBeNull();
    expect(submitInteraction).not.toHaveBeenCalled();
  });
});
