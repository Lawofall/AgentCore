// @vitest-environment jsdom
/**
 * partial + 上游限流：why 挂输入区轻提示（surface=composer），协作图条只报战绩。
 */
import { getAutonomy } from "@/api/autonomy";
import { getConversation, getMessages } from "@/api/conversations";
import { followConversation, streamMessage } from "@/api/stream";
import { getRecovery } from "@/api/turn";
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

const WHY = "上游限流，暂时无法继续本回合。请约 4 秒后再试。";
const DELIVERY = "已交付 3 个文件；1 项未完成";

function ev(type: SSEEvent["type"], payload: unknown): SSEEvent {
  return { type, timestamp: "t", payload } as SSEEvent;
}

const TEAM_PARTIAL_RATE_LIMIT: SSEEvent[] = [
  ev("message_start", {
    message_id: "m-asst",
    conversation_id: "conv-1",
    trace_id: "a".repeat(32),
  }),
  ev("run_plan", {
    execution_id: "exec_rl",
    plan_type: "multi_agent",
    task_summary: "调研",
    agents: [{ id: "w1", role: "调研员", thinking: false }],
    runs: [{ id: "r1", agent_id: "w1", task: "调研", depends_on: [] }],
  }),
  ev("run_started", {
    run_id: "r1",
    agent_id: "w1",
    parent_run_id: null,
    kind: "agent",
  }),
  ev("run_failed", {
    run_id: "r1",
    agent_id: "w1",
    error: WHY,
    product_landed: true,
    error_code: "LLM_RATE_LIMIT",
    retryable: true,
    retry_after: 4,
  }),
  ev("delivery_status", {
    execution_id: "exec_rl",
    state: "partial",
    summary: DELIVERY,
  }),
  ev("error", {
    code: "LLM_RATE_LIMIT",
    message: WHY,
    context: { retry_after: 4 },
  }),
  ev("message_end", { finish_reason: "error" }),
];

const SOLO_PARTIAL_RATE_LIMIT: SSEEvent[] = [
  ev("message_start", {
    message_id: "m-asst",
    conversation_id: "conv-1",
    trace_id: "a".repeat(32),
  }),
  ev("error", {
    code: "LLM_RATE_LIMIT",
    message: WHY,
    context: { retry_after: 4 },
  }),
  ev("delivery_status", {
    execution_id: "exec_solo",
    state: "partial",
    summary: DELIVERY,
  }),
  ev("message_end", { finish_reason: "error" }),
];

const PAUSED_RATE_LIMIT: SSEEvent[] = [
  ev("message_start", {
    message_id: "m-asst",
    conversation_id: "conv-1",
    trace_id: "a".repeat(32),
  }),
  ev("error", {
    code: "LLM_RATE_LIMIT",
    message: WHY,
    context: { retry_after: 4 },
  }),
  ev("message_end", { finish_reason: "paused", outcome: "paused" }),
];

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
    role: "user",
    content: "帮我调研",
    reasoning_content: null,
    citations: [],
    runs: null,
    created_at: "2026-01-01T00:00:00Z",
  };
}

function historyAssistant(
  events: SSEEvent[],
  extra?: { outcome?: "partial" | "paused" | "error"; paused?: boolean },
) {
  return {
    id: "m-asst",
    role: "assistant",
    content: extra?.outcome === "paused" ? "已落盘。" : "",
    reasoning_content: null,
    citations: [],
    runs: {
      events,
      finish_reason:
        extra?.outcome === "paused" ? "paused" : ("error" as const),
      process: null,
      error: { code: "LLM_RATE_LIMIT", message: WHY },
    },
    created_at: "2026-01-01T00:00:01Z",
    outcome: extra?.outcome ?? "partial",
    paused: extra?.paused ?? false,
    trace_id: "a".repeat(32),
  };
}

function openConv() {
  route.params = { id: "conv-1" };
  route.pathname = "/c/conv-1";
  return render(<ChatPage />);
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
    title: "调研",
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

describe("ChatPage · partial + 限流 composer hint", () => {
  it("团队图条只报部分完成战绩；why 和排查包在输入区", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [historyUser(), historyAssistant(TEAM_PARTIAL_RATE_LIMIT)],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByTestId("team-view")).toBeTruthy();
    expect(await screen.findByTestId("composer-outcome-hint")).toBeTruthy();
    const hint = screen.getByTestId("composer-outcome-hint");
    expect(hint.textContent).toContain(WHY);
    expect(hint.textContent).toContain("复制排查包");
    expect(hint.className).toContain("composer-delivery-hint");
    expect(hint.closest(".error")).toBeNull();

    const strip = document.querySelector(".team-strip")?.textContent ?? "";
    expect(strip).toContain("部分完成");
    expect(strip).not.toContain("上游限流");
    expect(strip).not.toContain("未能交付");
    expect(strip).not.toContain("已交付");
    expect(strip).not.toContain("复制排查包");
    expect(screen.queryByTestId("turn-outcome")).toBeNull();
  });

  it("无团队图时 why 也不进气泡红卡", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [historyUser(), historyAssistant(SOLO_PARTIAL_RATE_LIMIT)],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByTestId("composer-outcome-hint")).toBeTruthy();
    expect(screen.getByTestId("composer-outcome-hint").textContent).toContain(
      WHY,
    );
    expect(screen.queryByTestId("team-view")).toBeNull();
    expect(screen.queryByTestId("turn-outcome")).toBeNull();
    expect(screen.queryByTestId("paused-continue")).toBeNull();
  });

  it("kind=paused 仍走 PausedContinueCard，不进输入区轻提示", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [
        historyUser(),
        historyAssistant(PAUSED_RATE_LIMIT, {
          outcome: "paused",
          paused: true,
        }),
      ],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByTestId("paused-continue")).toBeTruthy();
    expect(screen.getByText("已暂停")).toBeTruthy();
    expect(screen.getByText(WHY)).toBeTruthy();
    expect(screen.queryByTestId("composer-outcome-hint")).toBeNull();
  });

  it("发送下一条后输入区轻提示消失", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [historyUser(), historyAssistant(TEAM_PARTIAL_RATE_LIMIT)],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();
    expect(await screen.findByTestId("composer-outcome-hint")).toBeTruthy();

    const input = screen.getByPlaceholderText(
      "说点什么…",
    ) as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "再试一次" } });
    await act(async () => {
      fireEvent.click(screen.getByLabelText("发送"));
    });
    await waitFor(() => {
      expect(screen.queryByTestId("composer-outcome-hint")).toBeNull();
    });
  });
});

describe("ChatPage · empty interrupt + team graph composer hint", () => {
  it("团队条不挂「发下一条」；同一句提示在输入区", async () => {
    vi.mocked(getMessages).mockResolvedValue({
      messages: [
        historyUser(),
        {
          id: "m-asst",
          role: "assistant",
          content: "",
          reasoning_content: null,
          citations: [],
          runs: {
            events: [
              ev("message_start", {
                message_id: "m-asst",
                conversation_id: "conv-1",
                trace_id: "a".repeat(32),
              }),
              ev("run_plan", {
                execution_id: "exec_int",
                plan_type: "multi_agent",
                task_summary: "调研",
                agents: [{ id: "w1", role: "调研员", thinking: false }],
                runs: [
                  { id: "r1", agent_id: "w1", task: "调研", depends_on: [] },
                ],
              }),
              ev("run_started", {
                run_id: "r1",
                agent_id: "w1",
                parent_run_id: null,
                kind: "agent",
              }),
              ev("run_failed", {
                run_id: "r1",
                agent_id: "w1",
                error: "调研失败",
              }),
              ev("message_end", { finish_reason: "interrupted" }),
            ],
            finish_reason: "interrupted",
            process: null,
            error: null,
          },
          created_at: "2026-08-01T00:00:01Z",
          outcome: "error",
          paused: false,
          trace_id: "a".repeat(32),
        },
      ],
      hasMoreBefore: false,
      memoryUpdates: [],
    });
    openConv();

    expect(await screen.findByTestId("team-view")).toBeTruthy();
    expect(await screen.findByTestId("composer-outcome-hint")).toBeTruthy();
    const hint = screen.getByTestId("composer-outcome-hint");
    expect(hint.textContent).toMatch(/直接发送下一条/);
    expect(hint.textContent).toContain("复制排查包");

    const strip = document.querySelector(".team-strip")?.textContent ?? "";
    expect(strip).not.toContain("直接发送下一条");
    expect(strip).not.toContain("复制排查包");
    expect(screen.queryByTestId("turn-outcome")).toBeNull();
  });
});
