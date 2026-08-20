import { StageCard } from "@/components/chat/StageCard";
import { StreamError } from "@/lib/errors";
import { resolveStageCardConversation } from "@/services/streamConversation";
import type { InteractionEntry } from "@/stores/interactions";
// @vitest-environment jsdom
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const finalizeLastMessage = vi.fn();
const setError = vi.fn();
const setAbort = vi.fn();
const createAssistantMessage = vi.fn();
const clearError = vi.fn();
const markResolved = vi.fn();
let generating = false;

vi.mock("@/services/streamConversation", () => ({
  resolveStageCardConversation: vi.fn(),
}));
vi.mock("@/hooks/useConversations", () => ({
  bumpConversationCache: vi.fn(),
}));
vi.mock("@/stores/conversation", () => ({
  getRuntime: () => ({ isGenerating: generating, messages: [] }),
  useConversationStore: {
    getState: () => ({
      clearError,
      createAssistantMessage: (...args: unknown[]) => {
        createAssistantMessage(...args);
        // Mirrors real store: optimistic assistant bubble flips isGenerating.
        generating = true;
      },
      setAbort,
      finalizeLastMessage,
      setError,
    }),
  },
}));
vi.mock("@/stores/conversation/turnPhaseActions", () => ({
  beginTurnPreflight: vi.fn(),
  getTurnPhase: vi.fn(() => "stopping"),
  completeTurnPhase: vi.fn(),
}));
vi.mock("@/stores/interactions", async () => {
  const actual = await vi.importActual<typeof import("@/stores/interactions")>(
    "@/stores/interactions",
  );
  return {
    ...actual,
    useInteractionStore: {
      getState: () => ({ markResolved }),
    },
  };
});

function entry(
  status: InteractionEntry["status"] = "pending",
  resolution?: Record<string, unknown>,
): InteractionEntry {
  return {
    kind: "stage_card",
    id: "sc_1",
    conversationId: "c1",
    messageId: "m1",
    status,
    payload: {
      motion: "是否应开辩",
      sides: [
        { key: "pro", name: "正方", stance: "应开" },
        { key: "con", name: "反方", stance: "暂缓" },
      ],
      form: "debate",
      rationale: "真对立轴",
      thorough: true,
      max_rounds: 5,
    },
    resolution,
  };
}

describe("StageCard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    generating = false;
  });

  it("renders motion and three actions when pending", () => {
    render(<StageCard entry={entry("pending")} />);
    expect(screen.getByTestId("stage-card")).toBeTruthy();
    expect(screen.getByText("是否应开辩")).toBeTruthy();
    expect(screen.getByText("按此开辩")).toBeTruthy();
    expect(screen.getByText("先补充调研")).toBeTruthy();
    expect(screen.getByText("调整命题")).toBeTruthy();
  });

  it("shows orphaned copy without action buttons", () => {
    render(<StageCard entry={entry("orphaned")} />);
    expect(screen.getByText(/已失效/)).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
    expect(screen.queryByText("先补充调研")).toBeNull();
    expect(screen.queryByText("调整命题")).toBeNull();
  });

  it("shows resolved copy without action buttons", () => {
    render(
      <StageCard entry={entry("resolved", { decision: "start_debate" })} />,
    );
    expect(screen.getByText("已按此开辩")).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
    expect(screen.queryByText("先补充调研")).toBeNull();
    expect(screen.queryByText("调整命题")).toBeNull();
  });

  it("shows research_first resolved copy", () => {
    render(
      <StageCard entry={entry("resolved", { decision: "research_first" })} />,
    );
    expect(screen.getByText("已选择先补充调研")).toBeTruthy();
    expect(screen.queryByText("按此开辩")).toBeNull();
  });

  it("finalizes generating on non-422 stream error so composer unlocks", async () => {
    vi.mocked(resolveStageCardConversation).mockRejectedValueOnce(
      new StreamError("network"),
    );
    render(<StageCard entry={entry("pending")} />);
    await act(async () => {
      fireEvent.click(screen.getByText("按此开辩"));
    });
    await waitFor(() => {
      expect(finalizeLastMessage).toHaveBeenCalledWith("c1");
    });
    expect(setError).toHaveBeenCalled();
  });

  it("finalizes generating on abort without surfacing a banner", async () => {
    vi.mocked(resolveStageCardConversation).mockRejectedValueOnce(
      new DOMException("Aborted", "AbortError"),
    );
    render(<StageCard entry={entry("pending")} />);
    await act(async () => {
      fireEvent.click(screen.getByText("按此开辩"));
    });
    await waitFor(() => {
      expect(finalizeLastMessage).toHaveBeenCalledWith("c1");
    });
    expect(setError).not.toHaveBeenCalled();
  });

  it("keeps card pending on 422 and still clears generating", async () => {
    vi.mocked(resolveStageCardConversation).mockRejectedValueOnce(
      new StreamError("http", 422, { serverMessage: "命题检定未通过" }),
    );
    render(<StageCard entry={entry("pending")} />);
    await act(async () => {
      fireEvent.click(screen.getByText("按此开辩"));
    });
    await waitFor(() => {
      expect(finalizeLastMessage).toHaveBeenCalledWith("c1");
    });
    const submitFail = screen.getByText("命题检定未通过");
    expect(submitFail.className).toContain("text-muted-foreground");
    expect(submitFail.className).not.toContain("destructive");
    expect(screen.getByText("按此开辩")).toBeTruthy();
    expect(markResolved).not.toHaveBeenCalled();
  });
});
