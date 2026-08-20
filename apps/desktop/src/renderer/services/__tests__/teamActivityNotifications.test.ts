// @vitest-environment jsdom
import { getConversations } from "@/hooks/useConversations";
import { queryClient } from "@/lib/queryClient";
import { notifyInfo } from "@/lib/toast";
import { startTeamActivityNotifications } from "@/services/teamActivityNotifications";
import { applyAiAttention, useAiAttentionStore } from "@/stores/aiAttention";
import {
  applyAiTurnActivity,
  useAiTurnActivityStore,
} from "@/stores/aiTurnActivity";
import { useConversationStore } from "@/stores/conversation";
import { useInteractionStore } from "@/stores/interactions";
import { type PendingResume, usePausedTurnStore } from "@/stores/pausedTurns";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useConversations", () => ({
  getConversations: vi.fn(() => []),
}));
vi.mock("@/lib/toast", () => ({
  notifyInfo: vi.fn(),
}));
vi.mock("@/lib/nativeNotification", () => ({
  showNativeNotification: vi.fn(() => Promise.resolve()),
}));
vi.mock("@/lib/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn(() => Promise.resolve()) },
}));

const getConversationsMock = vi.mocked(getConversations);
const notifyInfoMock = vi.mocked(notifyInfo);
const invalidateMock = vi.mocked(queryClient.invalidateQueries);

const CID = "conv-away";
const OTHER = "conv-other";

function resume(
  over: Partial<PendingResume> & Pick<PendingResume, "kind" | "checkpointId">,
): PendingResume {
  return {
    messageId: "msg-1",
    conversationId: CID,
    userMessage: "hi",
    userMessageId: "u1",
    steps: [],
    pending: [],
    workers: [],
    tools: [],
    primitive: "delegate",
    motion: "",
    form: "",
    sides: [],
    maxRounds: 0,
    thorough: true,
    question: "",
    assumptions: [],
    questions: [],
    intent: "decision",
    origin: "server",
    ...over,
  };
}

function seedTitle(id: string, title: string): void {
  getConversationsMock.mockReturnValue([
    {
      id,
      title,
      updatedAt: "2020-01-01T00:00:00.000Z",
      messageCount: 0,
      lastMessagePreview: null,
    },
  ]);
}

function setGenerating(id: string, generating: boolean): void {
  useConversationStore.setState((s) => ({
    byId: {
      ...s.byId,
      [id]: {
        ...(s.byId[id] ?? {
          messages: [],
          memoryUpdates: [],
          abort: null,
          error: null,
          retry: null,
          errorAction: null,
          messageFocus: null,
          hasMoreBefore: false,
          hasMoreAfter: false,
          loadingOlder: false,
          loadingNewer: false,
          pendingTurnWarning: null,
          executionVia: null,
        }),
        isGenerating: generating,
      },
    },
  }));
}

describe("startTeamActivityNotifications", () => {
  let stop: () => void;

  beforeEach(() => {
    notifyInfoMock.mockReset();
    invalidateMock.mockReset();
    getConversationsMock.mockReset();
    getConversationsMock.mockReturnValue([]);
    useConversationStore.setState({ currentConversationId: OTHER, byId: {} });
    usePausedTurnStore.getState().clear();
    useInteractionStore.getState().clear();
    useAiAttentionStore.getState().clear();
    useAiTurnActivityStore.getState().clear();
    window.location.hash = `#/conversations/${OTHER}`;
    stop = startTeamActivityNotifications();
  });

  afterEach(() => {
    stop();
    usePausedTurnStore.getState().clear();
    useInteractionStore.getState().clear();
    useAiAttentionStore.getState().clear();
    useAiTurnActivityStore.getState().clear();
    useConversationStore.setState({ currentConversationId: null, byId: {} });
  });

  it("paused 收口不弹已完成（isGenerating↓ 时已有 pausedTurns）", async () => {
    seedTitle(CID, "团队辩论");
    setGenerating(CID, true);
    // Same sync turn as finalizeLastMessage → surfaceResumeFromLiveTurn:
    // pause frame lands before the completion microtask.
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "plan_review", checkpointId: "cp-pr" }));
    setGenerating(CID, false);

    await Promise.resolve(); // flush queueMicrotask
    await Promise.resolve();

    const messages = notifyInfoMock.mock.calls.map((c) => String(c[0]));
    expect(messages.some((m) => m.includes("已完成"))).toBe(false);
    expect(messages).toContain("「团队辩论」等待你确认后才会继续");
  });

  it("云对话完成认 reason=completed 弹已完成", () => {
    seedTitle(CID, "调研");
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "completed",
    });
    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「调研」已完成",
      expect.any(Object),
    );
  });

  it("reason=error 弹执行失败；paused/stopped 不报已完成", () => {
    seedTitle(CID, "调研");
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "error",
    });
    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「调研」执行失败",
      expect.any(Object),
    );

    notifyInfoMock.mockClear();
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "paused",
    });
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "stopped",
    });
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("云对话 isGenerating↓ 不再当完成（改认 activity reason）", async () => {
    seedTitle(CID, "调研");
    setGenerating(CID, true);
    setGenerating(CID, false);
    await Promise.resolve();
    await Promise.resolve();
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("sidecar 本端收口仍弹已完成，云 done 不双计", async () => {
    seedTitle(CID, "本机");
    setGenerating(CID, true);
    useConversationStore.setState((s) => ({
      byId: {
        ...s.byId,
        [CID]: { ...s.byId[CID], executionVia: "sidecar" },
      },
    }));
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "completed",
    });
    expect(notifyInfoMock).not.toHaveBeenCalled();

    setGenerating(CID, false);
    await Promise.resolve();
    await Promise.resolve();
    expect(notifyInfoMock).toHaveBeenCalledTimes(1);
    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「本机」已完成",
      expect.any(Object),
    );
  });

  it("本地容器对话忽略云 done", () => {
    getConversationsMock.mockReturnValue([
      {
        id: CID,
        title: "本机容器",
        updatedAt: "2020-01-01T00:00:00.000Z",
        messageCount: 0,
        lastMessagePreview: null,
        localContainerRootId: "root-1",
      },
    ]);
    applyAiTurnActivity({
      conversation_id: CID,
      state: "done",
      reason: "completed",
    });
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("挂起 ask_user / plan_review 弹等待你确认后才会继续", () => {
    seedTitle(CID, "拍板会话");
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "ask_user", checkpointId: "cp-ask" }));
    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「拍板会话」等待你确认后才会继续",
      expect.any(Object),
    );

    notifyInfoMock.mockClear();
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "plan_review", checkpointId: "cp-pr2" }));
    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「拍板会话」等待你确认后才会继续",
      expect.any(Object),
    );
  });

  it("team_preview 挂起弹等待你确认后才会开工（且不与已完成双弹）", async () => {
    seedTitle(CID, "开工");
    setGenerating(CID, true);
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "team_preview", checkpointId: "cp-tp" }));
    setGenerating(CID, false);

    await Promise.resolve();
    await Promise.resolve();

    const messages = notifyInfoMock.mock.calls.map((c) => String(c[0]));
    expect(messages).toContain("「开工」等待你确认后才会开工");
    expect(messages.some((m) => m.includes("已完成"))).toBe(false);
  });

  it("幕终 pending stage_card 不弹已完成，弹需要你确认推进", async () => {
    seedTitle(CID, "调研收口");
    setGenerating(CID, true);
    useInteractionStore.getState().upsertRequired({
      kind: "stage_card",
      conversationId: CID,
      messageId: "msg-sc",
      payload: {
        stage_card_id: "sc-1",
        motion: "命题",
        form: "debate",
      },
    });
    setGenerating(CID, false);

    await Promise.resolve();
    await Promise.resolve();

    const messages = notifyInfoMock.mock.calls.map((c) => String(c[0]));
    expect(messages).toContain("「调研收口」需要你确认推进");
    expect(messages.some((m) => m.includes("已完成"))).toBe(false);
  });

  it("escalation 挂起弹需要你的决定（回合仍 streaming，完成通道不会触发）", () => {
    seedTitle(CID, "工程改造");
    setGenerating(CID, true);
    useInteractionStore.getState().upsertRequired({
      kind: "escalation",
      conversationId: CID,
      messageId: "msg-esc",
      payload: {
        escalation_id: "esc-1",
        run_id: "r1",
        question: "要不要直接改线上配置？",
        awaiting: "user",
      },
    });

    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「工程改造」需要你的决定",
      expect.any(Object),
    );
  });

  it("escalation 由 CEO 仲裁（awaiting=ceo）不打扰——与侧栏灯同一判定", () => {
    seedTitle(CID, "工程改造");
    useInteractionStore.getState().upsertRequired({
      kind: "escalation",
      conversationId: CID,
      messageId: "msg-esc2",
      payload: {
        escalation_id: "esc-2",
        run_id: "r2",
        question: "口径不一致",
        awaiting: "ceo",
      },
    });

    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("热阻塞卡与 firehose 同 id 仍只弹一次（新增两类也进同一张去重表）", () => {
    seedTitle(CID, "双通道升级");
    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: CID,
      turn_id: "t1",
      interaction_id: "esc-3",
      kind: "escalation",
      title: "要不要直接改线上配置？",
    });
    expect(notifyInfoMock).toHaveBeenCalledTimes(1);

    useInteractionStore.getState().upsertRequired({
      kind: "escalation",
      conversationId: CID,
      messageId: "msg-esc3",
      payload: {
        escalation_id: "esc-3",
        run_id: "r3",
        question: "要不要直接改线上配置？",
        awaiting: "user",
      },
    });

    expect(notifyInfoMock).toHaveBeenCalledTimes(1);
  });

  it("重连回放不重弹：启动前已 pending 的升级卡进 seed", () => {
    seedTitle(CID, "重连");
    stop();
    useInteractionStore.getState().upsertRequired({
      kind: "escalation",
      conversationId: CID,
      messageId: "msg-esc4",
      payload: {
        escalation_id: "esc-4",
        run_id: "r4",
        question: "继续吗",
        awaiting: "user",
      },
    });
    stop = startTeamActivityNotifications();
    notifyInfoMock.mockClear();

    // 回放把同一张卡再送一遍（幂等 upsert）——不得再弹。
    useInteractionStore.getState().upsertRequired({
      kind: "escalation",
      conversationId: CID,
      messageId: "msg-esc4",
      payload: {
        escalation_id: "esc-4",
        run_id: "r4",
        question: "继续吗",
        awaiting: "user",
      },
    });

    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("firehose ai_attention：没在本端流过的对话也提醒", () => {
    seedTitle(CID, "手机上起的活");
    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: CID,
      turn_id: "t1",
      interaction_id: "ap-1",
      kind: "approval",
      title: "要不要执行 rm -rf build/？",
    });

    expect(notifyInfoMock).toHaveBeenCalledWith(
      "「手机上起的活」· 要不要执行 rm -rf build/？",
      expect.any(Object),
    );
  });

  it("会话列表还没这条 → 用信号自带标题顶上，并让列表失效", () => {
    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: "conv-unknown",
      turn_id: "t1",
      interaction_id: "ap-2",
      kind: "approval",
      title: "需要你放行",
    });

    expect(notifyInfoMock).toHaveBeenCalledWith(
      "需要你放行",
      expect.any(Object),
    );
    expect(invalidateMock).toHaveBeenCalled();
  });

  it("同一张卡两路到达（firehose + 对话流）只弹一次", () => {
    seedTitle(CID, "双通道");
    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: CID,
      turn_id: "t1",
      interaction_id: "ap-3",
      kind: "approval",
      title: "放行终端命令",
    });
    expect(notifyInfoMock).toHaveBeenCalledTimes(1);

    // 对话流随后把同一张卡的正文送到（interaction_id 与 approval_id 同源）。
    useInteractionStore.getState().upsertRequired({
      kind: "approval",
      conversationId: CID,
      messageId: "msg-ap",
      payload: { approval_id: "ap-3", tool_name: "terminal", arguments: {} },
    });

    expect(notifyInfoMock).toHaveBeenCalledTimes(1);
  });

  it("resolved 撤掉后同 id 再 required 可再弹（去重表随信号收敛）", () => {
    seedTitle(CID, "反复");
    const frame = (state: "required" | "resolved") =>
      applyAiAttention({
        type: "ai_attention",
        state,
        conversation_id: CID,
        turn_id: "t1",
        interaction_id: "ap-4",
        kind: "approval",
        title: "放行",
      });

    frame("required");
    expect(notifyInfoMock).toHaveBeenCalledTimes(1);
    frame("resolved");
    frame("required");
    expect(notifyInfoMock).toHaveBeenCalledTimes(2);
  });

  it("人就在那个对话页 → 不打扰", () => {
    seedTitle(OTHER, "正在看的");
    applyAiAttention({
      type: "ai_attention",
      state: "required",
      conversation_id: OTHER,
      turn_id: "t1",
      interaction_id: "ap-5",
      kind: "approval",
      title: "放行",
    });

    expect(notifyInfoMock).not.toHaveBeenCalled();
  });

  it("同 checkpoint 不重复弹（seed + dedup）", () => {
    seedTitle(CID, "开工");
    // Seed before subscribe would happen — restart notifier with pending already there
    stop();
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "team_preview", checkpointId: "cp-seed" }));
    stop = startTeamActivityNotifications();
    notifyInfoMock.mockClear();

    // Re-add same checkpoint (idempotent replace) — must not re-toast
    usePausedTurnStore
      .getState()
      .addLiveResume(resume({ kind: "team_preview", checkpointId: "cp-seed" }));
    expect(notifyInfoMock).not.toHaveBeenCalled();
  });
});
