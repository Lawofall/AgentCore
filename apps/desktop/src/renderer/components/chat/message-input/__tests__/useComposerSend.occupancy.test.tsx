// @vitest-environment jsdom
/**
 * 本机还在写：灯灭也走插队，不开新一份、不 POST 云。
 */

import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const occupancy = vi.hoisted(() =>
  vi.fn(
    async (): Promise<{
      occupied: boolean;
      rootId?: string;
      subpath?: string;
      unknown?: boolean;
    }> => ({ occupied: false }),
  ),
);

vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
  upsertConversationFront: vi.fn(),
  applyDeletedConversationLocally: vi.fn(),
  getConversations: () => [],
}));
vi.mock("@/lib/composerPendingHint", () => ({
  confirmSendDespitePendingIfNeeded: () => true,
}));
vi.mock("@/lib/offlineMode", () => ({ isReadOnlyOffline: () => false }));
vi.mock("@/lib/toast", () => ({ notifyError: vi.fn() }));
vi.mock("@/services/api", () => ({ api: { post: vi.fn() } }));
vi.mock("@/services/conversations", () => ({
  provisionalConversationTitle: (s: string) => s.slice(0, 8),
  requestAutoTitle: vi.fn(),
  deleteConversation: vi.fn(),
}));
vi.mock("@/services/messages", () => ({ loadLatestWindow: vi.fn() }));
vi.mock("@/services/models", () => ({
  getLastUsedProfileId: () => null,
}));
vi.mock("@/services/permissionAxes", () => ({
  resolveDefaultPermissionAxes: vi.fn(),
  setComposerDraftAxes: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveSidecarRoot: vi.fn(async () => null),
  getLastSidecarTarget: vi.fn(() => null),
}));
vi.mock("@/services/turns", () => ({ sendTurn: vi.fn(async () => undefined) }));
vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));
vi.mock("@/services/turns/occupancy", () => ({
  querySidecarOccupancy: occupancy,
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../settleAttachments", () => ({
  settleAttachments: vi.fn(async () => ({ ok: true, outgoing: [] })),
}));

import { notifyError } from "@/lib/toast";
import { api } from "@/services/api";
import { sendTurn } from "@/services/turns";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { __resetComposerSendLatchesForTests } from "@/stores/composerSend";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { useExecutionStore } from "@/stores/execution";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import { useComposerSend } from "../useComposerSend";

const turn = vi.mocked(sendTurn);
const midFlight = vi.mocked(sendMidFlightMessage);
const apiPost = vi.mocked(api.post);

const CONV = "c-occupancy";

function seedConv() {
  useConversationStore.setState({
    currentConversationId: CONV,
    byId: {
      [CONV]: {
        ...EMPTY_RUNTIME,
        isGenerating: false,
        turnPhase: "failed",
      },
    },
  });
}

function useSendHarness({
  isGenerating = false,
  isLocal = true,
}: {
  isGenerating?: boolean;
  isLocal?: boolean;
} = {}) {
  const [value, setValue] = useState("再加一句");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([]);
  const [agentMentions, setAgentMentions] = useState<PendingAgentMention[]>([]);
  const send = useComposerSend({
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    isGenerating,
    backgroundMode: false,
    isLocal,
    closeMenu: () => {},
  });
  return send;
}

beforeEach(() => {
  occupancy.mockReset();
  occupancy.mockResolvedValue({ occupied: false });
  turn.mockReset();
  midFlight.mockReset();
  midFlight.mockResolvedValue({ kind: "received", interjectionId: "ij-1" });
  apiPost.mockReset();
  vi.mocked(notifyError).mockClear();
  __resetComposerSendLatchesForTests();
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  } as never);
  useExecutionStore.setState({ byId: {} });
});

describe("useComposerSend 本机占槽", () => {
  it("灯灭但本机还在写 → mid-flight，不 sendTurn、不 POST 云", async () => {
    seedConv();
    occupancy.mockResolvedValue({
      occupied: true,
      rootId: "root-1",
      subpath: "conversations/x",
    });
    const { result } = renderHook(() =>
      useSendHarness({ isGenerating: false }),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    expect(occupancy).toHaveBeenCalledWith(CONV);
    expect(midFlight).toHaveBeenCalledTimes(1);
    expect(midFlight).toHaveBeenCalledWith(
      CONV,
      "再加一句",
      undefined,
      expect.any(String),
      undefined,
      {
        sidecarTarget: { rootId: "root-1", subpath: "conversations/x" },
      },
    );
    expect(turn).not.toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
    expect(useConversationStore.getState().byId[CONV]?.isGenerating).toBe(true);
    expect(useConversationStore.getState().byId[CONV]?.turnPhase).toBe(
      "streaming",
    );
  });

  it("本机灯亮但槽已空 → 开新一份，不 mid-flight", async () => {
    seedConv();
    occupancy.mockResolvedValue({ occupied: false });
    const { result } = renderHook(() => useSendHarness({ isGenerating: true }));

    await act(async () => {
      await result.current.handleSend();
    });

    expect(turn).toHaveBeenCalledTimes(1);
    expect(midFlight).not.toHaveBeenCalled();
  });

  it("问不清不当闲：不 sendTurn、不 mid-flight、不 POST 云", async () => {
    seedConv();
    occupancy.mockResolvedValue({ occupied: false, unknown: true });
    const { result } = renderHook(() => useSendHarness({ isGenerating: true }));

    await act(async () => {
      await result.current.handleSend();
    });

    expect(occupancy).toHaveBeenCalledWith(CONV);
    expect(turn).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
    expect(notifyError).toHaveBeenCalled();
  });

  it("纯云生成中 occupancy 未占槽 → mid-flight，不 sendTurn", async () => {
    seedConv();
    occupancy.mockResolvedValue({ occupied: false });
    const { result } = renderHook(() =>
      useSendHarness({ isGenerating: true, isLocal: false }),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    expect(occupancy).toHaveBeenCalledWith(CONV);
    expect(midFlight).toHaveBeenCalledTimes(1);
    expect(turn).not.toHaveBeenCalled();
  });

  it("生产 Composer isLocal 假但本机占槽 → mid-flight 带 sidecar，不 POST 云", async () => {
    seedConv();
    occupancy.mockResolvedValue({
      occupied: true,
      rootId: "root-1",
      subpath: "conversations/x",
    });
    const { result } = renderHook(() =>
      useSendHarness({ isGenerating: false, isLocal: false }),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    expect(occupancy).toHaveBeenCalledWith(CONV);
    expect(midFlight).toHaveBeenCalledTimes(1);
    expect(midFlight).toHaveBeenCalledWith(
      CONV,
      "再加一句",
      undefined,
      expect.any(String),
      undefined,
      {
        sidecarTarget: { rootId: "root-1", subpath: "conversations/x" },
      },
    );
    expect(turn).not.toHaveBeenCalled();
    expect(apiPost).not.toHaveBeenCalled();
  });

  it("云端灯灭但协作图还在转 → mid-flight，不 sendTurn", async () => {
    const mid = "asst-live-team";
    useConversationStore.setState({
      currentConversationId: CONV,
      byId: {
        [CONV]: {
          ...EMPTY_RUNTIME,
          isGenerating: false,
          turnPhase: "idle",
          messages: [
            {
              id: mid,
              role: "assistant",
              content: "换打法",
              createdAt: new Date().toISOString(),
              executionId: "e-live",
              isStreaming: false,
            },
          ],
        },
      },
    });
    useExecutionStore.getState().startExecution(
      {
        id: "e-live",
        planType: "multi_agent",
        taskSummary: "调研",
        agents: [{ id: "a-w", role: "研究员" }],
        runs: [{ id: "r-w", agentId: "a-w", task: "查", dependsOn: [] }],
      },
      mid,
    );
    occupancy.mockResolvedValue({ occupied: false });
    const { result } = renderHook(() =>
      useSendHarness({ isGenerating: false, isLocal: false }),
    );

    await act(async () => {
      await result.current.handleSend();
    });

    expect(midFlight).toHaveBeenCalledTimes(1);
    expect(turn).not.toHaveBeenCalled();
  });
});
