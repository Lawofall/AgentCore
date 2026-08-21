// @vitest-environment jsdom
/**
 * turn_queue_started：凭帧正文插主时间线用户泡（不靠 beginTurn2 闭包）；
 * 与 beginTurn2 共用 queue_id 幂等，勿双泡。
 */
import { getAutonomy } from "@/api/autonomy";
import { getConversation, getMessages } from "@/api/conversations";
import { sendMidFlightMessage } from "@/api/midFlight";
import { followConversation, streamMessage } from "@/api/stream";
import { fetchQueuedTurns, getRecovery } from "@/api/turn";
import {
  __resetQueuedTurnsForTests,
  listQueuedTurns,
  upsertQueuedTurn,
} from "@/lib/queuedTurns";
import { ChatPage, __resetChatPageHandoffForTests } from "@/pages/ChatPage";
import type { SSEEvent } from "@agentcore/contract-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const navigate = vi.fn();
const { locationState, route } = vi.hoisted(() => ({
  locationState: { current: null as unknown },
  route: { params: {} as { id?: string }, pathname: "/" },
}));
vi.mock("react-router-dom", async () => {
  const actual =
    await vi.importActual<typeof import("react-router-dom")>(
      "react-router-dom",
    );
  return {
    ...actual,
    useNavigate: () => navigate,
    useParams: () => route.params,
    useLocation: () => ({
      pathname: route.pathname,
      state: locationState.current,
    }),
  };
});

vi.mock("@/api/client", () => ({
  getTokens: () => ({ access_token: "a", refresh_token: "r" }),
  apiFetch: vi.fn(),
}));

vi.mock("@/api/conversations", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/conversations")>()),
  createConversation: vi.fn(),
  deleteConversation: vi.fn(),
  getConversation: vi.fn(),
  getMessages: vi.fn(),
}));

vi.mock("@/api/autonomy", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/autonomy")>()),
  getAutonomy: vi.fn(),
}));

vi.mock("@/api/stream", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/stream")>()),
  streamMessage: vi.fn(),
  followConversation: vi.fn(),
}));

vi.mock("@/api/turn", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/turn")>()),
  getRecovery: vi.fn(),
  fetchQueuedTurns: vi.fn(),
}));

vi.mock("@/api/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));

vi.mock("@/api/modelProfiles", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/modelProfiles")>()),
  useModelProfiles: () => ({
    data: null,
    loading: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function ev(type: SSEEvent["type"], payload: unknown): SSEEvent {
  return { type, timestamp: "t", payload } as SSEEvent;
}

function hangUntilAbort(...args: unknown[]) {
  const signal = args.find(
    (a): a is AbortSignal =>
      typeof a === "object" &&
      a !== null &&
      "aborted" in a &&
      "addEventListener" in a,
  );
  return new Promise<void>((_resolve, reject) => {
    const abort = () => reject(new DOMException("Aborted", "AbortError"));
    if (!signal) return;
    if (signal.aborted) {
      abort();
      return;
    }
    signal.addEventListener("abort", abort);
  });
}

function historyUser() {
  return {
    id: "m-user",
    role: "user" as const,
    content: "历史一句",
    reasoning_content: null,
    citations: [],
    runs: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function historyAssistant() {
  return {
    id: "m-asst",
    role: "assistant" as const,
    content: "好的。",
    reasoning_content: null,
    citations: [],
    runs: {
      events: [
        ev("message_start", {
          message_id: "m-asst",
          conversation_id: "conv-1",
        }),
        ev("content_delta", { delta: "好的。" }),
        ev("message_end", { finish_reason: "end_turn" }),
      ],
      finish_reason: "end_turn" as const,
      process: null,
    },
    created_at: "2026-01-01T00:00:01Z",
    outcome: "ok" as const,
    paused: false,
    trace_id: "a".repeat(32),
  };
}

function userBubbles(): HTMLElement[] {
  return [...document.querySelectorAll(".bubble.user")] as HTMLElement[];
}

function composer(): HTMLTextAreaElement {
  return screen.getByPlaceholderText("说点什么…") as HTMLTextAreaElement;
}

let followOnEvent: ((event: SSEEvent) => void) | null = null;

function openConv() {
  route.params = { id: "conv-1" };
  route.pathname = "/c/conv-1";
  return render(<ChatPage />);
}

async function waitFollowReady() {
  await waitFor(() => {
    expect(followConversation).toHaveBeenCalled();
    expect(followOnEvent).toBeTruthy();
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  followOnEvent = null;
  __resetChatPageHandoffForTests();
  __resetQueuedTurnsForTests();
  locationState.current = null;
  route.params = { id: "conv-1" };
  route.pathname = "/c/conv-1";
  Object.defineProperty(Element.prototype, "scrollTo", {
    configurable: true,
    writable: true,
    value: () => {},
  });
  class IntersectionObserverStub {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
  vi.stubGlobal(
    "IntersectionObserver",
    IntersectionObserverStub as unknown as typeof IntersectionObserver,
  );
  vi.mocked(getAutonomy).mockRejectedValue(new Error("offline"));
  vi.mocked(getConversation).mockResolvedValue({
    id: "conv-1",
    title: "排队",
    archived: false,
    context_compacted: false,
    created_at: "2026-08-01T00:00:00Z",
    deep_research_auto: false,
    message_count: 2,
    pinned: false,
    updated_at: "2026-08-01T00:00:00Z",
    permission_axes: {
      file_write: "session",
      command: "auto",
      team_kickoff: "rules",
      host: "session",
    },
    model_profile_id: null,
    folder_id: null,
  } as Awaited<ReturnType<typeof getConversation>>);
  vi.mocked(getRecovery).mockResolvedValue({
    liveRunning: false,
    paused: [],
    pendingInteractions: [],
  });
  vi.mocked(fetchQueuedTurns).mockResolvedValue([]);
  vi.mocked(getMessages).mockResolvedValue({
    messages: [historyUser(), historyAssistant()],
    hasMoreBefore: false,
    memoryUpdates: [],
  });
  vi.mocked(followConversation).mockImplementation((...args) => {
    const onEvent = args.find(
      (a): a is (event: SSEEvent) => void => typeof a === "function",
    );
    if (onEvent && args.length >= 2) followOnEvent = args[1] as typeof onEvent;
    return hangUntilAbort(...args) as ReturnType<typeof followConversation>;
  });
  vi.mocked(streamMessage).mockImplementation(
    hangUntilAbort as typeof streamMessage,
  );
  vi.mocked(sendMidFlightMessage).mockResolvedValue({
    kind: "error",
    message: "not stubbed",
  });
});

afterEach(() => {
  cleanup();
  __resetQueuedTurnsForTests();
});

describe("ChatPage · turn_queue_started 插泡", () => {
  it("排队期只出条，不进主时间线", async () => {
    openConv();
    await waitFollowReady();
    act(() => {
      upsertQueuedTurn({
        queueId: "q-bar",
        conversationId: "conv-1",
        content: "还在排队",
        position: 1,
        queueDepth: 1,
      });
    });
    expect(screen.getByTestId("queued-turns-bar")).toBeTruthy();
    expect(screen.getByText(/还在排队/)).toBeTruthy();
    expect(
      userBubbles().filter((el) => el.textContent?.includes("还在排队")),
    ).toHaveLength(0);
  });

  it("started 无 beginTurn2 也能凭帧正文插泡，且不 GET 消息窗补用户行", async () => {
    openConv();
    await waitFollowReady();
    const loads = vi.mocked(getMessages).mock.calls.length;
    act(() => {
      upsertQueuedTurn({
        queueId: "q-remote",
        conversationId: "conv-1",
        content: "条上的旧文",
        position: 1,
        queueDepth: 1,
      });
    });
    expect(screen.getByTestId("queued-turns-bar")).toBeTruthy();

    await act(async () => {
      followOnEvent?.(
        ev("turn_queue_started", {
          queue_id: "q-remote",
          conversation_id: "conv-1",
          remaining_depth: 0,
          content: "另一端出队的话",
          attachments: [{ name: "note.txt", truncated: true }],
          agent_mentions: [{ agent_id: "w1", role: "研究员" }],
        }),
      );
    });

    await waitFor(() => {
      expect(screen.getByText("另一端出队的话")).toBeTruthy();
    });
    expect(screen.queryByTestId("queued-turns-bar")).toBeNull();
    expect(listQueuedTurns("conv-1")).toEqual([]);
    expect(
      userBubbles().filter((el) => el.textContent?.includes("另一端出队的话")),
    ).toHaveLength(1);
    expect(screen.getByTestId("agent-mention-chip").textContent).toContain(
      "研究员",
    );
    expect(screen.getByText("note.txt")).toBeTruthy();
    expect(vi.mocked(getMessages).mock.calls.length).toBe(loads);
  });

  it("beginTurn2 + started 不双泡", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [historyUser(), historyAssistant()],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    vi.mocked(sendMidFlightMessage).mockImplementation(
      async (_cid, text, hooks) => {
        hooks.onQueued({ queueId: "q-2", position: 1, queueDepth: 1 });
        hooks.beginTurn2();
        hooks.onTurn2Event(
          ev("turn_queue_started", {
            queue_id: "q-2",
            conversation_id: "conv-1",
            remaining_depth: 0,
            content: text,
          }),
        );
        return {
          kind: "queued",
          queueId: "q-2",
          position: 1,
          queueDepth: 1,
        };
      },
    );

    openConv();
    await waitFollowReady();
    await waitFor(() => expect(composer()).toBeTruthy());

    fireEvent.change(composer(), { target: { value: "第一问" } });
    fireEvent.click(screen.getByLabelText("发送"));
    await waitFor(() => {
      expect(screen.getByLabelText("停止")).toBeTruthy();
      expect(screen.getByText("第一问")).toBeTruthy();
    });

    fireEvent.change(composer(), { target: { value: "第二问" } });
    fireEvent.click(screen.getByLabelText("发送"));

    await waitFor(() => {
      expect(sendMidFlightMessage).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(
        userBubbles().filter((el) => el.textContent?.includes("第二问")),
      ).toHaveLength(1);
    });
  });
});
