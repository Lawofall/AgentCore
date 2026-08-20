// @vitest-environment jsdom
/**
 * 草稿首发建会话：一次发送动作只能建出一条会话。
 *
 * 线上 7 天 8 起「同一条任务建出两条内容相同的会话、各自跑完整轮双倍计费」，成因是
 * 组件实例级的防连点 ref（重挂载即归零）+ 创建 POST 返回前界面毫无变化（用户自然再按
 * 一次）。这里守三件事：创建窗口内重复触发只发一个 POST（含重挂载后再点）、按下发送
 * 立刻清空并进入建会话中态、创建失败草稿原样还回且重试复用同一个幂等键。
 */

import { act, renderHook } from "@testing-library/react";
import { type SetStateAction, useCallback } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { navigate, deleteConversation, applyDeletedConversationLocally } =
  vi.hoisted(() => ({
    navigate: vi.fn(),
    deleteConversation: vi.fn(),
    applyDeletedConversationLocally: vi.fn(),
  }));

vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
  upsertConversationFront: vi.fn(),
  applyDeletedConversationLocally,
}));
vi.mock("@/lib/composerPendingHint", () => ({
  confirmSendDespitePendingIfNeeded: () => true,
}));
vi.mock("@/lib/offlineMode", () => ({ isReadOnlyOffline: () => false }));
vi.mock("@/lib/redirectLocalWorkspaceAsk", () => ({
  redirectLocalWorkspaceAskAction: vi.fn(),
}));
vi.mock("@/lib/toast", () => ({ notifyError: vi.fn() }));
vi.mock("@/services/api", () => ({ api: { post: vi.fn() } }));
vi.mock("@/services/conversations", () => ({
  provisionalConversationTitle: (s: string) => s.slice(0, 8),
  requestAutoTitle: vi.fn(),
  deleteConversation,
}));
vi.mock("@/services/messages", () => ({ loadLatestWindow: vi.fn() }));
vi.mock("@/services/models", () => ({ getLastUsedProfileId: () => null }));
vi.mock("@/services/permissionAxes", () => ({
  resolveDefaultPermissionAxes: vi.fn(async () => ({
    file_write: "session",
    command: "auto",
    team_kickoff: "rules",
    host: "session",
  })),
  setComposerDraftAxes: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  resolveSidecarRoot: vi.fn(async () => null),
}));
vi.mock("@/services/turns", () => ({ sendTurn: vi.fn(async () => undefined) }));
vi.mock("@/services/turns/midFlight", () => ({
  sendMidFlightMessage: vi.fn(),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => navigate }));
vi.mock("../settleAttachments", () => ({
  settleAttachments: vi.fn(async () => ({ ok: true, outgoing: [] })),
}));

import { __resetDraftRequestIdsForTests } from "@/lib/draftRequestId";
import { notifyError } from "@/lib/toast";
import { api } from "@/services/api";
import { sendTurn } from "@/services/turns";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import { __resetComposerSendLatchesForTests } from "@/stores/composerSend";
import {
  setComposerSendError,
  useComposerSendErrorStore,
} from "@/stores/composerSendError";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { useFoldersStore } from "@/stores/folders";
import {
  __clearAttachmentUploadsForTests,
  rememberAttachmentRecover,
} from "../attachmentUploads";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import { settleAttachments } from "../settleAttachments";
import { useComposerSend } from "../useComposerSend";

const post = vi.mocked(api.post);
const toastError = vi.mocked(notifyError);
const settle = vi.mocked(settleAttachments);
const turn = vi.mocked(sendTurn);

const DRAFT_KEY = draftKeyFor(null);
const NEW_CONV = "conv-new";
const TEXT = "帮我把这周的进展整理成周报";

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

const mention: PendingAgentMention = {
  id: "m-1",
  agentId: "a-1",
  role: "研究员",
};

/** 与 TurnComposer 同构：草稿读写都走 composer store，不用组件 state。 */
function useSendHarness() {
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const key = draftKeyFor(conversationId);
  const draft = useComposerDraftStore((s) => s.drafts[key]);
  const setValue = useCallback(
    (a: SetStateAction<string>) =>
      useComposerDraftStore.getState().setValue(key, a),
    [key],
  );
  const setAttachments = useCallback(
    (a: SetStateAction<PendingAttachment[]>) =>
      useComposerDraftStore.getState().setAttachments(key, a),
    [key],
  );
  const setAgentMentions = useCallback(
    (a: SetStateAction<PendingAgentMention[]>) =>
      useComposerDraftStore.getState().setAgentMentions(key, a),
    [key],
  );
  const send = useComposerSend({
    value: draft?.value ?? "",
    setValue,
    attachments: draft?.attachments ?? [],
    setAttachments,
    agentMentions: draft?.agentMentions ?? [],
    setAgentMentions,
    isGenerating: false,
    backgroundMode: false,
    isLocal: false,
    closeMenu: () => {},
  });
  return { send };
}

const REFUSAL_MSG = "账户余额不足";
const EXISTING = "conv-existing";

function seedDraftOn(key: string): void {
  const store = useComposerDraftStore.getState();
  store.setValue(key, TEXT);
  store.setAttachments(key, [shot]);
  store.setAgentMentions(key, [mention]);
}

function seedDraft(): void {
  seedDraftOn(DRAFT_KEY);
}

function mockUnstartedRefusal(): void {
  turn.mockImplementation(async (args) => {
    const conversationId = args.conversationId;
    const optimisticUserId = args.optimisticUserId;
    if (optimisticUserId) {
      useConversationStore
        .getState()
        .removeMessage(optimisticUserId, conversationId);
    }
    useConversationStore
      .getState()
      .setError(REFUSAL_MSG, null, conversationId, null);
    return { unstartedRefusal: true };
  });
}

function draft() {
  return useComposerDraftStore.getState().drafts[DRAFT_KEY];
}

function createdBody(call = 0) {
  return post.mock.calls[call][1] as { client_request_id?: string };
}

beforeEach(() => {
  post.mockReset();
  toastError.mockReset();
  settle.mockReset();
  settle.mockResolvedValue({ ok: true, outgoing: [] });
  turn.mockReset();
  turn.mockResolvedValue(undefined as never);
  navigate.mockReset();
  deleteConversation.mockReset();
  deleteConversation.mockResolvedValue(undefined);
  applyDeletedConversationLocally.mockReset();
  __resetComposerSendLatchesForTests();
  __resetDraftRequestIdsForTests();
  __clearAttachmentUploadsForTests();
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  } as never);
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
  useComposerSendErrorStore.setState({ byKey: {} });
  useFoldersStore.setState({
    draftWorkspaceIntent: { kind: "quick_cloud" },
  });
});

describe("useComposerSend 草稿首发建会话", () => {
  it("创建窗口内重复触发只产生一次创建请求", async () => {
    let release!: (conv: { id: string }) => void;
    post.mockReturnValue(
      new Promise((r) => {
        release = r as (conv: { id: string }) => void;
      }),
    );
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    // 连点 = 同一帧里打两发（Enter 连按 / Enter 撞上按钮），两次拿的是同一个还带着
    // 草稿的闭包，清空输入框拦不住它们——只能靠门闩。
    const fire = result.current.send.handleSend;
    let sending!: Promise<void>;
    await act(async () => {
      sending = fire();
      await fire();
    });

    expect(post).toHaveBeenCalledTimes(1);
    expect(post.mock.calls[0][0]).toBe("/v1/conversations");

    await act(async () => {
      release({ id: NEW_CONV });
      await sending;
    });
    expect(post).toHaveBeenCalledTimes(1);
    expect(
      Object.keys(useConversationStore.getState().byId).filter(
        (k) => k === NEW_CONV,
      ),
    ).toEqual([NEW_CONV]);
  });

  it("组件重挂载后重打一遍再发，仍只建一条（门闩不是组件实例内的 ref）", async () => {
    let release!: (conv: { id: string }) => void;
    post.mockReturnValue(
      new Promise((r) => {
        release = r as (conv: { id: string }) => void;
      }),
    );
    seedDraft();
    const first = renderHook(() => useSendHarness());

    let sending!: Promise<void>;
    await act(async () => {
      sending = first.result.current.send.handleSend();
    });
    // 切对话回来 / 路由重建：composer 换了一个实例，闸不能跟着归零。
    first.unmount();
    const second = renderHook(() => useSendHarness());
    expect(second.result.current.send.isSending).toBe(true);
    expect(second.result.current.send.isCreatingConversation).toBe(true);

    // 创建 POST 慢（线上有 3.7s / 5.6s 两例），用户以为没发出去，重打了一遍再按。
    await act(async () => {
      useComposerDraftStore.getState().setValue(DRAFT_KEY, TEXT);
    });
    await act(async () => {
      await second.result.current.send.handleSend();
    });
    expect(post).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({ id: NEW_CONV });
      await sending;
    });
    expect(post).toHaveBeenCalledTimes(1);
  });

  it("按下发送立刻清空输入框并给出建会话中态，不等 POST 回来", async () => {
    let release!: (conv: { id: string }) => void;
    post.mockReturnValue(
      new Promise((r) => {
        release = r as (conv: { id: string }) => void;
      }),
    );
    setComposerSendError(DRAFT_KEY, { message: "上次失败", action: null });
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    let sending!: Promise<void>;
    await act(async () => {
      sending = result.current.send.handleSend();
    });

    expect(post).toHaveBeenCalledTimes(1);
    expect(draft()).toBeUndefined();
    expect(result.current.send.isCreatingConversation).toBe(true);
    expect(
      useComposerSendErrorStore.getState().byKey[DRAFT_KEY],
    ).toBeUndefined();

    await act(async () => {
      release({ id: NEW_CONV });
      await sending;
    });
    // 建完就不再是「建会话中」，气泡已落在新会话上。
    expect(result.current.send.isCreatingConversation).toBe(false);
    expect(
      useConversationStore.getState().byId[NEW_CONV]?.messages,
    ).toHaveLength(1);
    expect(useConversationStore.getState().currentConversationId).toBe(
      NEW_CONV,
    );
  });

  it("开跑前拒绝：拆空会话，草稿和错误回到 __draft__，不复用已软删的幂等键", async () => {
    post.mockResolvedValue({ id: NEW_CONV } as never);
    mockUnstartedRefusal();
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(draft()?.value).toBe(TEXT);
    expect(draft()?.attachments).toEqual([shot]);
    expect(draft()?.agentMentions).toEqual([mention]);
    expect(
      useComposerDraftStore.getState().drafts[draftKeyFor(NEW_CONV)],
    ).toBeUndefined();
    expect(useComposerSendErrorStore.getState().byKey[DRAFT_KEY]).toEqual({
      message: REFUSAL_MSG,
      action: null,
    });
    expect(deleteConversation).toHaveBeenCalledWith(NEW_CONV);
    expect(applyDeletedConversationLocally).toHaveBeenCalledWith(NEW_CONV);
    expect(navigate).toHaveBeenCalledWith("/");
    expect(useConversationStore.getState().currentConversationId).toBeNull();
    expect(turn).toHaveBeenCalledTimes(1);

    const firstId = createdBody(0).client_request_id;
    post.mockResolvedValue({ id: "conv-2" } as never);
    turn.mockResolvedValue(undefined as never);
    await act(async () => {
      await result.current.send.handleSend();
    });
    expect(createdBody(1).client_request_id).toBeTruthy();
    expect(createdBody(1).client_request_id).not.toBe(firstId);
  });

  it("开跑前拒绝拆会话后重试：旧 workspacePath / 已消耗 staging 也要再驻留进新会话", async () => {
    const blob = new File([new Uint8Array([1])], "shot.png", {
      type: "image/png",
    });
    const stale: PendingAttachment = {
      ...shot,
      workspacePath: "attachments/shot.png",
      stagingId: "stg-consumed",
      fileBlob: blob,
    };
    post.mockResolvedValue({ id: NEW_CONV } as never);
    mockUnstartedRefusal();
    useComposerDraftStore.getState().setValue(DRAFT_KEY, TEXT);
    useComposerDraftStore.getState().setAttachments(DRAFT_KEY, [stale]);
    useComposerDraftStore.getState().setAgentMentions(DRAFT_KEY, [mention]);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    const restored = draft()?.attachments[0];
    expect(restored?.workspacePath).toBeUndefined();
    expect(restored?.fileBlob).toBe(blob);
    expect(restored?.stagingId).toBe("stg-consumed");
    expect(settle).toHaveBeenCalledWith(NEW_CONV, [stale], "send");

    post.mockResolvedValue({ id: "conv-2" } as never);
    turn.mockResolvedValue(undefined as never);
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(settle).toHaveBeenCalledTimes(2);
    expect(settle.mock.calls[1][0]).toBe("conv-2");
    expect(settle.mock.calls[1][1][0]).toEqual(
      expect.objectContaining({
        id: stale.id,
        workspacePath: undefined,
        fileBlob: blob,
        stagingId: "stg-consumed",
      }),
    );
  });

  it("开跑前拒绝：芯片没有 File 时从 recover blob 恢复再发", async () => {
    const blob = new File([new Uint8Array([9])], "shot.png", {
      type: "image/png",
    });
    const consumed: PendingAttachment = {
      ...shot,
      workspacePath: "attachments/shot.png",
      stagingId: "stg-consumed",
    };
    rememberAttachmentRecover(consumed.id, blob, NEW_CONV);
    post.mockResolvedValue({ id: NEW_CONV } as never);
    mockUnstartedRefusal();
    useComposerDraftStore.getState().setValue(DRAFT_KEY, TEXT);
    useComposerDraftStore.getState().setAttachments(DRAFT_KEY, [consumed]);
    useComposerDraftStore.getState().setAgentMentions(DRAFT_KEY, [mention]);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(draft()?.attachments[0]?.workspacePath).toBeUndefined();
    expect(draft()?.attachments[0]?.fileBlob).toBe(blob);

    post.mockResolvedValue({ id: "conv-2" } as never);
    turn.mockResolvedValue(undefined as never);
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(settle.mock.calls[1][0]).toBe("conv-2");
    expect(settle.mock.calls[1][1][0].fileBlob).toBe(blob);
    expect(settle.mock.calls[1][1][0].workspacePath).toBeUndefined();
  });

  it("已有会话跟发开跑前拒绝：草稿还回当前会话，不删", async () => {
    useConversationStore.setState({
      currentConversationId: EXISTING,
      byId: {
        [EXISTING]: {
          ...EMPTY_RUNTIME,
          messages: [
            {
              id: "old-1",
              role: "user",
              content: "上一句",
              createdAt: "2026-01-01T00:00:00.000Z",
              executionId: null,
              isStreaming: false,
            },
          ],
        },
      },
    } as never);
    mockUnstartedRefusal();
    seedDraftOn(EXISTING);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    const restored = useComposerDraftStore.getState().drafts[EXISTING];
    expect(restored?.value).toBe(TEXT);
    expect(restored?.attachments).toEqual([shot]);
    expect(restored?.agentMentions).toEqual([mention]);
    expect(draft()).toBeUndefined();
    expect(useComposerSendErrorStore.getState().byKey[EXISTING]).toEqual({
      message: REFUSAL_MSG,
      action: null,
    });
    expect(deleteConversation).not.toHaveBeenCalled();
    expect(applyDeletedConversationLocally).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
    expect(useConversationStore.getState().currentConversationId).toBe(
      EXISTING,
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("开跑前拒绝拆会话：create 带了 folder_id 则恢复 folder 意图", async () => {
    useFoldersStore.getState().setDraftWorkspaceIntent({
      kind: "folder",
      folderId: "fld-1",
    });
    post.mockResolvedValue({ id: NEW_CONV } as never);
    mockUnstartedRefusal();
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(useFoldersStore.getState().draftWorkspaceIntent).toEqual({
      kind: "folder",
      folderId: "fld-1",
    });
    expect(deleteConversation).toHaveBeenCalledWith(NEW_CONV);
    expect(navigate).toHaveBeenCalledWith("/");
  });

  it("开跑前拒绝：删失败则留在新会话，仍还草稿和错误", async () => {
    post.mockResolvedValue({ id: NEW_CONV } as never);
    deleteConversation.mockRejectedValue(new Error("网络不可达"));
    mockUnstartedRefusal();
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    const restored =
      useComposerDraftStore.getState().drafts[draftKeyFor(NEW_CONV)];
    expect(restored?.value).toBe(TEXT);
    expect(draft()).toBeUndefined();
    expect(useComposerSendErrorStore.getState().byKey[NEW_CONV]).toEqual({
      message: REFUSAL_MSG,
      action: null,
    });
    expect(deleteConversation).toHaveBeenCalledWith(NEW_CONV);
    expect(applyDeletedConversationLocally).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalledWith("/");
    expect(useConversationStore.getState().currentConversationId).toBe(
      NEW_CONV,
    );
  });

  it("创建失败：草稿（正文 + 附件 + 点名）原样还回并给中文提示", async () => {
    post.mockRejectedValue(new Error("网络不可达"));
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(draft()?.value).toBe(TEXT);
    expect(draft()?.attachments).toEqual([shot]);
    expect(draft()?.agentMentions).toEqual([mention]);
    expect(toastError).toHaveBeenCalledWith(
      expect.objectContaining({ message: "网络不可达" }),
      "新建对话失败",
    );
    expect(result.current.send.isSending).toBe(false);
    expect(useConversationStore.getState().currentConversationId).toBeNull();
  });

  it("失败后重试复用同一个 client_request_id（服务端幂等挡住第二条会话）", async () => {
    post.mockRejectedValueOnce(new Error("网络不可达"));
    post.mockResolvedValueOnce({ id: NEW_CONV } as never);
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(post).toHaveBeenCalledTimes(2);
    const first = createdBody(0).client_request_id;
    expect(first).toBeTruthy();
    expect(createdBody(1).client_request_id).toBe(first);
  });

  it("草稿清空后另起一条：换新的 client_request_id，不会认领上一条会话", async () => {
    post.mockRejectedValueOnce(new Error("网络不可达"));
    post.mockResolvedValueOnce({ id: NEW_CONV } as never);
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });
    const failedId = createdBody(0).client_request_id;

    // 用户抹掉草稿重新打了一条别的内容 —— 这是新的意图，必须是新键。
    await act(async () => {
      const store = useComposerDraftStore.getState();
      store.setValue(DRAFT_KEY, "");
      store.setAttachments(DRAFT_KEY, []);
      store.setAgentMentions(DRAFT_KEY, []);
      store.setValue(DRAFT_KEY, "换个话题：订下周去上海的机票");
    });
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(post).toHaveBeenCalledTimes(2);
    expect(createdBody(1).client_request_id).toBeTruthy();
    expect(createdBody(1).client_request_id).not.toBe(failedId);
  });

  it("上一次发送收尾时不会替下一次发送把闸放掉", async () => {
    post.mockResolvedValue({ id: NEW_CONV } as never);
    // 第一条回合上路后就放闸（流式交给生成态），但它 await 的 sendTurn 还没结束。
    let finishFirstTurn!: () => void;
    turn.mockReturnValueOnce(
      new Promise<void>((r) => {
        finishFirstTurn = r;
      }) as never,
    );
    // 第二条卡在附件收尾里，此刻闸在它手上。
    let releaseSecondSettle!: (r: { ok: true; outgoing: [] }) => void;
    settle.mockResolvedValueOnce({ ok: true, outgoing: [] });
    settle.mockReturnValueOnce(
      new Promise((r) => {
        releaseSecondSettle = r as (v: { ok: true; outgoing: [] }) => void;
      }),
    );
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    let first!: Promise<void>;
    await act(async () => {
      first = result.current.send.handleSend();
    });
    expect(result.current.send.isSending).toBe(false);

    let second!: Promise<void>;
    await act(async () => {
      useComposerDraftStore.getState().setValue(NEW_CONV, "第二条任务");
    });
    await act(async () => {
      second = result.current.send.handleSend();
    });
    expect(result.current.send.isSending).toBe(true);

    await act(async () => {
      finishFirstTurn();
      await first;
    });
    // 第一条的 finally 不能碰第二条的闸，否则连点闸就在这一刻悄悄打开了。
    expect(result.current.send.isSending).toBe(true);

    await act(async () => {
      releaseSecondSettle({ ok: true, outgoing: [] });
      await second;
    });
    expect(result.current.send.isSending).toBe(false);
  });

  it("发送成功后再开一条草稿：键已轮换", async () => {
    post.mockResolvedValue({ id: NEW_CONV } as never);
    seedDraft();
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });
    const firstId = createdBody(0).client_request_id;

    // 回到草稿态再发一条（新建对话入口）。
    await act(async () => {
      useConversationStore.getState().switchConversation(null);
      useComposerDraftStore.getState().setValue(DRAFT_KEY, "第二条任务");
    });
    post.mockResolvedValue({ id: "conv-2" } as never);
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(post).toHaveBeenCalledTimes(2);
    expect(createdBody(1).client_request_id).not.toBe(firstId);
  });
});
