// @vitest-environment jsdom
/**
 * 只读协作桌（viewer）handleSend 兜底：notifyError 后 return，不发请求。
 */

import { act, renderHook } from "@testing-library/react";
import { type SetStateAction, useCallback } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { FolderMeta } from "@/services/folders";
import type { Conversation } from "@/stores/conversation";

const lists = vi.hoisted(() => ({
  folders: [] as FolderMeta[],
  conversations: [] as Conversation[],
}));

vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
  upsertConversationFront: vi.fn(),
  applyDeletedConversationLocally: vi.fn(),
  getConversations: () => lists.conversations,
}));
vi.mock("@/hooks/useFolders", () => ({
  getFolders: () => lists.folders,
  useFolders: () => lists.folders,
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
vi.mock("@/services/models", () => ({ getLastUsedProfileId: () => null }));
vi.mock("@/services/permissionAxes", () => ({
  resolveDefaultPermissionAxes: vi.fn(async () => ({
    file_write: "session",
    command: "auto",
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
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../settleAttachments", () => ({
  settleAttachments: vi.fn(async () => ({ ok: true, outgoing: [] })),
}));

import { notifyError } from "@/lib/toast";
import { api } from "@/services/api";
import { sendTurn } from "@/services/turns";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import { __resetComposerSendLatchesForTests } from "@/stores/composerSend";
import { useConversationStore } from "@/stores/conversation";
import { EMPTY_RUNTIME } from "@/stores/conversation/runtime";
import { useFoldersStore } from "@/stores/folders";
import type {
  PendingAgentMention,
  PendingAttachment,
} from "../composerAttachments";
import { COMPOSER_FOLDER_READ_ONLY_HINT } from "../composerFolderWrite";
import { useComposerSend } from "../useComposerSend";

const post = vi.mocked(api.post);
const toastError = vi.mocked(notifyError);
const turn = vi.mocked(sendTurn);

const TEXT = "帮我看一眼这份材料";

function cloudDesk(
  id: string,
  name: string,
  myRole: "owner" | "editor" | "viewer",
): FolderMeta {
  return {
    id,
    name,
    mode: "cloud",
    localRootId: null,
    localSubpath: null,
    myRole,
  };
}

function listedConv(id: string, folderId: string | null): Conversation {
  return {
    id,
    title: "对话",
    updatedAt: "2026-01-01T00:00:00Z",
    messageCount: 1,
    lastMessagePreview: null,
    folderId,
  };
}

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

beforeEach(() => {
  post.mockReset();
  toastError.mockReset();
  turn.mockReset();
  turn.mockResolvedValue(undefined as never);
  __resetComposerSendLatchesForTests();
  lists.folders = [];
  lists.conversations = [];
  useConversationStore.setState({
    currentConversationId: null,
    byId: {},
  } as never);
  useComposerDraftStore.setState({
    drafts: {},
    fillToken: 0,
    dockFlipToken: 0,
  });
  useFoldersStore.setState({
    draftWorkspaceIntent: { kind: "quick_cloud" },
  });
});

describe("useComposerSend 只读协作桌", () => {
  it("viewer 当前对话：notifyError，不 POST / 不 sendTurn", async () => {
    useConversationStore.setState({
      currentConversationId: "c-view",
      byId: { "c-view": { ...EMPTY_RUNTIME } },
    });
    lists.conversations = [listedConv("c-view", "f-shared")];
    lists.folders = [cloudDesk("f-shared", "队友桌", "viewer")];
    useComposerDraftStore.getState().setValue("c-view", TEXT);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(toastError).toHaveBeenCalledWith(COMPOSER_FOLDER_READ_ONLY_HINT);
    expect(post).not.toHaveBeenCalled();
    expect(turn).not.toHaveBeenCalled();
  });

  it("viewer 草稿 folder 意图：notifyError，不建会话", async () => {
    lists.folders = [cloudDesk("f-view", "只读桌", "viewer")];
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "folder", folderId: "f-view" },
    });
    useComposerDraftStore.getState().setValue(draftKeyFor(null), TEXT);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(toastError).toHaveBeenCalledWith(COMPOSER_FOLDER_READ_ONLY_HINT);
    expect(post).not.toHaveBeenCalled();
    expect(turn).not.toHaveBeenCalled();
  });

  it("viewer 仅在 getFolders（与我共享）而不在 grouped：同样拦", async () => {
    useConversationStore.setState({
      currentConversationId: "c-view",
      byId: { "c-view": { ...EMPTY_RUNTIME } },
    });
    lists.conversations = [listedConv("c-view", "f-shared")];
    lists.folders = [cloudDesk("f-shared", "队友桌", "viewer")];
    useComposerDraftStore.getState().setValue("c-view", TEXT);
    const { result } = renderHook(() => useSendHarness());

    await act(async () => {
      await result.current.send.handleSend();
    });

    expect(turn).not.toHaveBeenCalled();
    expect(toastError).toHaveBeenCalledWith(COMPOSER_FOLDER_READ_ONLY_HINT);
  });

  it("owner / editor / 裸聊 / 找不到 folder：仍 sendTurn", async () => {
    post.mockResolvedValue({ id: "c-new" } as never);

    useConversationStore.setState({
      currentConversationId: "c-own",
      byId: { "c-own": { ...EMPTY_RUNTIME } },
    });
    lists.conversations = [listedConv("c-own", "f-own")];
    lists.folders = [cloudDesk("f-own", "我的桌", "owner")];
    useComposerDraftStore.getState().setValue("c-own", TEXT);
    const own = renderHook(() => useSendHarness());
    await act(async () => {
      await own.result.current.send.handleSend();
    });
    expect(turn).toHaveBeenCalled();
    toastError.mockClear();
    turn.mockClear();
    own.unmount();

    useConversationStore.setState({
      currentConversationId: "c-edit",
      byId: { "c-edit": { ...EMPTY_RUNTIME } },
    });
    lists.conversations = [listedConv("c-edit", "f-edit")];
    lists.folders = [cloudDesk("f-edit", "可编桌", "editor")];
    useComposerDraftStore.getState().setValue("c-edit", TEXT);
    const edit = renderHook(() => useSendHarness());
    await act(async () => {
      await edit.result.current.send.handleSend();
    });
    expect(turn).toHaveBeenCalled();
    turn.mockClear();
    edit.unmount();

    useConversationStore.setState({
      currentConversationId: null,
      byId: {},
    } as never);
    lists.conversations = [];
    lists.folders = [];
    useFoldersStore.setState({
      draftWorkspaceIntent: { kind: "quick_cloud" },
    });
    useComposerDraftStore.getState().setValue(draftKeyFor(null), TEXT);
    const bare = renderHook(() => useSendHarness());
    await act(async () => {
      await bare.result.current.send.handleSend();
    });
    expect(post).toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
    post.mockClear();
    bare.unmount();

    useConversationStore.setState({
      currentConversationId: "c-gone",
      byId: { "c-gone": { ...EMPTY_RUNTIME } },
    });
    lists.conversations = [listedConv("c-gone", "f-missing")];
    lists.folders = [];
    useComposerDraftStore.getState().setValue("c-gone", TEXT);
    const missing = renderHook(() => useSendHarness());
    await act(async () => {
      await missing.result.current.send.handleSend();
    });
    expect(turn).toHaveBeenCalled();
    expect(toastError).not.toHaveBeenCalled();
  });
});
