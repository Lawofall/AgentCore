// @vitest-environment jsdom
/**
 * 辩论进行中主框走 debate-steer，不 sendTurn / 不 mid-flight。
 */

import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
  upsertConversationFront: vi.fn(),
  applyDeletedConversationLocally: vi.fn(),
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
vi.mock("@/services/debate", () => ({
  submitDebateSteer: vi.fn(),
}));
vi.mock("@/services/messages", () => ({ loadLatestWindow: vi.fn() }));
vi.mock("@/services/models", () => ({ getLastUsedProfileId: () => null }));
vi.mock("@/services/permissionAxes", () => ({
  resolveDefaultPermissionAxes: vi.fn(),
  setComposerDraftAxes: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveSidecarRoot: vi.fn(async () => null),
}));
vi.mock("@/services/turns", () => ({ sendTurn: vi.fn(async () => undefined) }));
vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../settleAttachments", () => ({
  settleAttachments: vi.fn(async () => ({ ok: true, outgoing: [] })),
}));

import { notifyError } from "@/lib/toast";
import { submitDebateSteer } from "@/services/debate";
import { sendTurn } from "@/services/turns";
import { sendMidFlightMessage } from "@/services/turns/midFlight";
import { useComposerDraftStore } from "@/stores/composer";
import { __resetComposerSendLatchesForTests } from "@/stores/composerSend";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { type ExecutionPlan, useExecutionStore } from "@/stores/execution";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import { useComposerSend } from "../useComposerSend";

const steer = vi.mocked(submitDebateSteer);
const turn = vi.mocked(sendTurn);
const midFlight = vi.mocked(sendMidFlightMessage);
const toastError = vi.mocked(notifyError);

const CONV = "c-debate";
const MID = "a-debate";

const LIVE_DEBATE_PLAN: ExecutionPlan = {
  id: "exec-d",
  planType: "debate",
  taskSummary: "该不该上",
  agents: [
    { id: "a-pro", role: "正方" },
    { id: "a-con", role: "反方" },
  ],
  runs: [
    {
      id: "r-pro",
      agentId: "a-pro",
      task: "立论",
      dependsOn: [],
      stance: "pro",
      group: "debate:debate",
      round: 1,
    },
    {
      id: "r-con",
      agentId: "a-con",
      task: "反驳",
      dependsOn: [],
      stance: "con",
      group: "debate:debate",
      round: 1,
    },
  ],
};

const shot: PendingAttachment = {
  id: "att-1",
  key: "dropped:shot.png:1",
  name: "shot.png",
  path: "shot.png",
  text: "",
  truncated: false,
  kind: "file",
  binary: true,
};

function seedLiveDebate() {
  useConversationStore.setState({
    currentConversationId: CONV,
    byId: {
      [CONV]: {
        ...EMPTY_RUNTIME,
        isGenerating: true,
        messages: [
          {
            id: MID,
            role: "assistant",
            content: "",
            createdAt: new Date().toISOString(),
            executionId: "exec-d",
            isStreaming: true,
          },
        ],
      },
    },
  });
  useExecutionStore.getState().startExecution(LIVE_DEBATE_PLAN, MID);
}

function useSendHarness({
  isGenerating = true,
  initialValue = "再问定价谁兜底",
  initialAttachments = [] as PendingAttachment[],
  initialMentions = [] as PendingAgentMention[],
}: {
  isGenerating?: boolean;
  initialValue?: string;
  initialAttachments?: PendingAttachment[];
  initialMentions?: PendingAgentMention[];
} = {}) {
  const [value, setValue] = useState(initialValue);
  const [attachments, setAttachments] = useState(initialAttachments);
  const [agentMentions, setAgentMentions] = useState(initialMentions);
  const send = useComposerSend({
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    isGenerating,
    backgroundMode: false,
    isLocal: false,
    closeMenu: () => {},
  });
  return { value, attachments, send, setValue };
}

beforeEach(() => {
  steer.mockReset();
  steer.mockResolvedValue(true);
  turn.mockReset();
  midFlight.mockReset();
  toastError.mockReset();
  __resetComposerSendLatchesForTests();
  useExecutionStore.setState({ byId: {} });
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  } as never);
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
});

describe("useComposerSend 辩论进行中", () => {
  it("Enter/发送 enqueue continue，不 sendTurn、不 mid-flight", async () => {
    seedLiveDebate();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(steer).toHaveBeenCalledTimes(1);
    expect(steer).toHaveBeenCalledWith(CONV, {
      executionId: "exec-d",
      decision: {
        kind: "continue",
        focus: "",
        ask: "再问定价谁兜底",
        askTarget: "",
      },
    });
    expect(turn).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
    expect(result.current.value).toBe("");
    expect(useConversationStore.getState().byId[CONV]?.messages).toHaveLength(
      1,
    );
  });

  it("出结论 enqueue conclude", async () => {
    seedLiveDebate();
    const { result } = renderHook(() => useSendHarness({ initialValue: "" }));

    await act(async () => {
      await result.current.send.handleSend({ debateSteer: "conclude" });
    });

    expect(steer).toHaveBeenCalledWith(CONV, {
      executionId: "exec-d",
      decision: { kind: "conclude", ask: "", askTarget: "" },
    });
    expect(turn).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
  });

  it("accepted=false：toast 未生效，不清草稿、不说已发送", async () => {
    seedLiveDebate();
    steer.mockResolvedValue(false);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(toastError).toHaveBeenCalledWith("未生效，辩论已停止接收");
    expect(result.current.value).toBe("再问定价谁兜底");
    expect(turn).not.toHaveBeenCalled();
  });

  it("附件不当辩论插话送出", async () => {
    seedLiveDebate();
    const { result } = renderHook(() =>
      useSendHarness({
        initialValue: "带张图",
        initialAttachments: [shot],
      }),
    );

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(steer).not.toHaveBeenCalled();
    expect(turn).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(
      "辩论进行中不能带附件，请去掉后再对这场说话",
    );
    expect(result.current.value).toBe("带张图");
    expect(result.current.attachments).toEqual([shot]);
  });

  it("空 continue 早退：仅 mention 芯片不 enqueue", async () => {
    seedLiveDebate();
    const { result } = renderHook(() =>
      useSendHarness({
        initialValue: "",
        initialMentions: [{ id: "m-1", agentId: "a-1", role: "研究员" }],
      }),
    );

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(steer).not.toHaveBeenCalled();
    expect(turn).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
  });

  it("收场后恢复 sendTurn，不再 enqueue debate-steer", async () => {
    seedLiveDebate();
    useExecutionStore
      .getState()
      .recordDebateResult({ execution_id: "exec-d" } as never, MID);
    useExecutionStore.getState().setStatus("completed", MID);
    const { result } = renderHook(() =>
      useSendHarness({ isGenerating: false }),
    );

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(steer).not.toHaveBeenCalled();
    expect(midFlight).not.toHaveBeenCalled();
    expect(turn).toHaveBeenCalledTimes(1);
  });
});
