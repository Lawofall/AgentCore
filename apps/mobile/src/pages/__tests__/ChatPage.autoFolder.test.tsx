// @vitest-environment jsdom
/**
 * 裸聊自动建文件夹：fold 仍投影 autoFolder，对话不再渲染落点告知。
 */
import { getAutonomy } from "@/api/autonomy";
import { getConversation, getMessages } from "@/api/conversations";
import { followConversation, streamMessage } from "@/api/stream";
import { getRecovery } from "@/api/turn";
import { ChatPage, __resetChatPageHandoffForTests } from "@/pages/ChatPage";
import type { SSEEvent } from "@agentcore/contract-types";
import { cleanup, render, screen } from "@testing-library/react";
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

const AUTO_FOLDER = { folder_id: "f1", name: "季度复盘" };

function historyUser() {
  return {
    id: "m-user",
    role: "user",
    content: "写一份复盘",
    reasoning_content: null,
    citations: [],
    runs: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function historyAssistant(events: SSEEvent[]) {
  return {
    id: "m-asst",
    role: "assistant",
    content: "写好了。",
    reasoning_content: null,
    citations: [],
    runs: {
      events,
      finish_reason: "end_turn" as const,
      process: null,
      auto_folder: AUTO_FOLDER,
    },
    created_at: "2026-01-01T00:00:01Z",
    outcome: "ok" as const,
    paused: false,
    trace_id: "a".repeat(32),
  };
}

function folderCreatedEvents(extra: SSEEvent[] = []): SSEEvent[] {
  return [
    ev("message_start", {
      message_id: "m-asst",
      conversation_id: "conv-1",
      trace_id: "a".repeat(32),
    }),
    ev("auto_folder_created", AUTO_FOLDER),
    ...extra,
    ev("message_end", { finish_reason: "end_turn" }),
  ];
}

function openConv() {
  route.params = { id: "conv-1" };
  route.pathname = "/c/conv-1";
  return render(<ChatPage />);
}

function expectNoAutoFolderNotice() {
  expect(screen.queryByText("文件已存到新建的文件夹")).toBeNull();
  expect(screen.queryByText("已为这次对话新建文件夹")).toBeNull();
  expect(screen.queryByTestId("auto-folder-notice")).toBeNull();
  expect(screen.queryByTestId("auto-folder-notice-card")).toBeNull();
}

beforeEach(() => {
  vi.clearAllMocks();
  __resetChatPageHandoffForTests();
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
    title: "复盘",
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
  vi.mocked(followConversation).mockImplementation(
    hangUntilAbort as typeof followConversation,
  );
  vi.mocked(streamMessage).mockImplementation(
    hangUntilAbort as typeof streamMessage,
  );
});

afterEach(cleanup);

describe("ChatPage · auto-folder notice withdrawn", () => {
  it("does not render a standalone landing card when the turn has no files", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [historyUser(), historyAssistant(folderCreatedEvents())],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByText("写好了。")).toBeTruthy();
    expectNoAutoFolderNotice();
  });

  it("does not render a card-header landing line when files already landed", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [
        historyUser(),
        historyAssistant(
          folderCreatedEvents([
            ev("delivery_status", {
              execution_id: "exec_af",
              state: "delivered",
              summary: "已交付",
              delivered_files: ["a.md"],
              gaps: [],
              actions: [],
              artifacts: [{ path: "a.md", status: "accepted" }],
            }),
          ]),
        ),
      ],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByText("本回合产出文件")).toBeTruthy();
    expect(screen.getByText("写好了。")).toBeTruthy();
    expectNoAutoFolderNotice();
  });
});
