// @vitest-environment jsdom
/**
 * 点发送必须「立刻有反应」：乐观气泡与清空输入框排在等待附件之前，等待期间发送键处于
 * in-flight 态；附件最终失败时不留假气泡，草稿（正文 + 附件 + 点名）原样还给用户。
 */

import { act, renderHook } from "@testing-library/react";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

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
// 真 ApiError（只换掉 api.post）：失败链路要证明的正是「原始错误对象没被拆」。
vi.mock("@/services/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/services/api")>();
  return { ...actual, api: { post: vi.fn() } };
});
vi.mock("@/services/conversations", () => ({
  provisionalConversationTitle: (s: string) => s.slice(0, 8),
  requestAutoTitle: vi.fn(),
  deleteConversation: vi.fn(),
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
vi.mock("../settleAttachments", () => ({ settleAttachments: vi.fn() }));

import { notifyError } from "@/lib/toast";
import { ApiError } from "@/services/api";
import { sendTurn } from "@/services/turns";
import { useComposerDraftStore } from "@/stores/composer";
import { useConversationStore } from "@/stores/conversation";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import { settleAttachments } from "../settleAttachments";
import { useComposerSend } from "../useComposerSend";

const settle = vi.mocked(settleAttachments);
const turn = vi.mocked(sendTurn);
const toastError = vi.mocked(notifyError);

const CONV = "c1";

const shot: PendingAttachment = {
  id: "att-1",
  key: "dropped:shot.png:1",
  name: "shot.png",
  path: "shot.png",
  text: "",
  truncated: false,
  kind: "file",
  binary: true,
  fileBlob: new File([new Uint8Array([1])], "shot.png", { type: "image/png" }),
};

const mention: PendingAgentMention = {
  id: "m-1",
  agentId: "a-1",
  role: "研究员",
};

/** 真 state，模拟 TurnComposer 把草稿存进 store 的那套 setter。 */
function useSendHarness() {
  const [value, setValue] = useState("看看这张图");
  const [attachments, setAttachments] = useState<PendingAttachment[]>([shot]);
  const [agentMentions, setAgentMentions] = useState<PendingAgentMention[]>([
    mention,
  ]);
  const send = useComposerSend({
    value,
    setValue,
    attachments,
    setAttachments,
    agentMentions,
    setAgentMentions,
    isGenerating: false,
    backgroundMode: false,
    isLocal: false,
    closeMenu: () => {},
  });
  return { value, attachments, agentMentions, send };
}

function messages() {
  return useConversationStore.getState().byId[CONV]?.messages ?? [];
}

beforeEach(() => {
  settle.mockReset();
  turn.mockReset();
  turn.mockResolvedValue(undefined as never);
  toastError.mockReset();
  useConversationStore.setState({
    currentConversationId: CONV,
    byId: {},
  } as never);
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
});

describe("useComposerSend 附件收尾", () => {
  it("乐观气泡与清空输入框发生在等待附件之前，并进入 in-flight 态", async () => {
    let release!: (r: Awaited<ReturnType<typeof settleAttachments>>) => void;
    settle.mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    const { result } = renderHook(() => useSendHarness());

    let sending!: Promise<void>;
    await act(async () => {
      sending = result.current.send.handleSend();
    });

    // 附件还没收尾，但用户已经看到气泡 + Thinking、输入框已清空、发送键在转。
    expect(messages()).toHaveLength(2);
    expect(messages()[0]).toMatchObject({
      role: "user",
      content: "看看这张图",
    });
    expect(messages()[1]).toMatchObject({
      role: "assistant",
      isStreaming: true,
    });
    expect(result.current.value).toBe("");
    expect(result.current.attachments).toHaveLength(0);
    expect(result.current.send.isSending).toBe(true);
    expect(turn).not.toHaveBeenCalled();

    await act(async () => {
      release({
        ok: true,
        outgoing: [
          {
            name: "shot.png",
            path: "attachments/shot.png",
            text: "",
            truncated: false,
            kind: "file",
            binary: true,
            workspace_path: "attachments/shot.png",
          },
        ],
      });
      await sending;
    });

    // 回合上路后就退出 in-flight：流式本身由生成态接管，别锁住排队 / 插队。
    expect(result.current.send.isSending).toBe(false);
    expect(turn).toHaveBeenCalledTimes(1);
    expect(turn.mock.calls[0][0].attachments).toEqual([
      expect.objectContaining({ workspace_path: "attachments/shot.png" }),
    ]);
    // 落地后补正气泡里的路径，附件下载链接才指得对。
    expect(messages()[0].attachments?.[0]).toMatchObject({
      path: "attachments/shot.png",
      workspacePath: "attachments/shot.png",
    });
  });

  it("附件最终失败：撤掉假气泡、还回草稿、给中文提示、不发回合", async () => {
    // 后端拒绝：原始 ApiError 必须整个交给 toast，拆成 message 就只剩通用兜底了。
    const denied = new ApiError(
      403,
      JSON.stringify({
        error: { code: "WORKSPACE_READONLY", message: "工作区当前只读" },
      }),
    );
    settle.mockResolvedValue({
      ok: false,
      reason: denied.message,
      cause: denied,
      staleIds: [],
    });
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(messages()).toHaveLength(0);
    expect(turn).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(denied, "附件驻留失败");

    const draft = useComposerDraftStore.getState().drafts[CONV];
    expect(draft?.value).toBe("看看这张图");
    expect(draft?.attachments).toEqual([shot]);
    expect(draft?.agentMentions).toEqual([mention]);
    expect(result.current.send.isSending).toBe(false);
  });

  it("暂存已失效的附件不还回草稿（留着也发不出去）", async () => {
    settle.mockResolvedValue({
      ok: false,
      reason: "附件暂存已失效，请重新附加",
      staleIds: [shot.id],
    });
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    const draft = useComposerDraftStore.getState().drafts[CONV];
    expect(draft?.value).toBe("看看这张图");
    expect(draft?.attachments ?? []).toHaveLength(0);
    // 纯客户端原因（没有后端错误对象）原样交出去，别再包一层。
    expect(toastError).toHaveBeenCalledWith(
      "附件暂存已失效，请重新附加",
      "附件驻留失败",
    );
  });

  it("等待附件期间连点发送不会发出第二个回合", async () => {
    let release!: (r: Awaited<ReturnType<typeof settleAttachments>>) => void;
    settle.mockReturnValue(
      new Promise((r) => {
        release = r;
      }),
    );
    const { result } = renderHook(() => useSendHarness());

    let sending!: Promise<void>;
    await act(async () => {
      sending = result.current.send.handleSend();
    });
    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(settle).toHaveBeenCalledTimes(1);

    await act(async () => {
      release({ ok: true, outgoing: [] });
      await sending;
    });
    expect(turn).toHaveBeenCalledTimes(1);
  });
});
