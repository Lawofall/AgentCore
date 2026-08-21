import { StreamError, describeStreamError } from "@/lib/errors";
import type { OutboxFlushTurnResult } from "@shared/outbox-contract";
import type { SidecarTurnResult } from "@shared/sidecar-contract";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// Resume reuses the普通本地回合 scaffolding; mock the heavy collaborators so the
// resume link's observable contract is asserted in isolation: the `resume` RPC
// params, event forwarding/filtering, outbox flush keyed on the *original* user
// bubble id (pinned on pause write-back), and the failure→StreamError("sidecar") /
// abort→AbortError mapping. The real conversation store is used (seeded below) so
// the optimistic-id reconcile is faithful.
vi.mock("@/services/streamConversation", () => ({
  dispatchSSEEvent: vi.fn(),
  flushPendingContent: vi.fn(),
  flushPendingFrames: vi.fn(),
}));
vi.mock("@/services/sidecarRouting", () => ({
  setActiveSidecarTurn: vi.fn(),
  clearActiveSidecarTurn: vi.fn(),
}));
vi.mock("@/services/sidecarStatus", () => ({
  takeRecentSidecarFailure: vi.fn(() => null),
}));
vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
  getConversations: vi.fn(() => []),
  syncConversationListPreview: vi.fn(),
}));
vi.mock("@/hooks/useFolders", () => ({
  getFolders: vi.fn(() => []),
}));
vi.mock("@/lib/toast", () => ({
  notifyWarning: vi.fn(),
  notifySuccess: vi.fn(),
}));
// Deterministically control whether a cloud-proxy token is mintable. Default in
// beforeEach is a valid token (turns require one). Cases that assert the no-token
// gate mock `null` and expect INFERENCE_TOKEN_EXPIRED without startTurn/resume RPC.
vi.mock("@/services/inferenceToken", () => ({
  resolveSidecarInference: vi.fn(),
  clearSidecarInference: vi.fn(),
  // Default: not a token failure → catch path rethrows (existing cases assert sidecar StreamError / AbortError).
  looksLikeInferenceTokenFailure: vi.fn(() => false),
}));

vi.mock("@/services/foldersToken", () => ({
  resolveSidecarFoldersAuth: vi.fn(),
  clearSidecarFoldersAuth: vi.fn(),
  looksLikeFoldersTokenFailure: vi.fn(() => false),
}));

vi.mock("@/services/accountToken", () => ({
  resolveSidecarAccountAuth: vi.fn(),
  clearSidecarAccountAuth: vi.fn(),
  looksLikeAccountTokenFailure: vi.fn(() => false),
}));

vi.mock("@/services/chatContext", () => ({
  fetchChatContext: vi.fn(async () => []),
  CHAT_CONTEXT_UNAVAILABLE_MESSAGE: "未能加载对话历史，请稍后重试。",
}));

import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { notifyWarning } from "@/lib/toast";
import {
  looksLikeAccountTokenFailure,
  resolveSidecarAccountAuth,
} from "@/services/accountToken";
import {
  CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
  fetchChatContext,
} from "@/services/chatContext";
import {
  looksLikeFoldersTokenFailure,
  resolveSidecarFoldersAuth,
} from "@/services/foldersToken";
import {
  clearSidecarInference,
  looksLikeInferenceTokenFailure,
  resolveSidecarInference,
} from "@/services/inferenceToken";
import { takeRecentSidecarFailure } from "@/services/sidecarStatus";
import { dispatchSSEEvent } from "@/services/streamConversation";
import { useAuthStore } from "@/stores/auth";
import { useConversationStore } from "@/stores/conversation";
import { resetSidecarEventPumpForTests } from "../sidecarEventPump";
import {
  resumeConversationViaSidecar,
  streamConversationViaSidecar,
} from "../streamConversationViaSidecar";

const dispatchSSEEventMock = vi.mocked(dispatchSSEEvent);
const takeRecentSidecarFailureMock = vi.mocked(takeRecentSidecarFailure);
const resolveSidecarInferenceMock = vi.mocked(resolveSidecarInference);
const clearSidecarInferenceMock = vi.mocked(clearSidecarInference);
const looksLikeInferenceTokenFailureMock = vi.mocked(
  looksLikeInferenceTokenFailure,
);
const resolveSidecarFoldersAuthMock = vi.mocked(resolveSidecarFoldersAuth);
const looksLikeFoldersTokenFailureMock = vi.mocked(
  looksLikeFoldersTokenFailure,
);
const resolveSidecarAccountAuthMock = vi.mocked(resolveSidecarAccountAuth);
const looksLikeAccountTokenFailureMock = vi.mocked(
  looksLikeAccountTokenFailure,
);
const notifyWarningMock = vi.mocked(notifyWarning);
const getConversationsMock = vi.mocked(getConversations);
const getFoldersMock = vi.mocked(getFolders);
const fetchChatContextMock = vi.mocked(fetchChatContext);

type EventPush = { conversationId: string; turnId: string; event: unknown };

function turnResult(): SidecarTurnResult {
  return {
    turnId: "m-asst",
    messageId: "m-asst",
    content: "续答完成",
    reasoningContent: null,
    finishReason: "stop",
    model: "deepseek-v4-flash",
    rounds: 1,
    usage: {
      inputTokens: 10,
      outputTokens: 5,
      reasoningTokens: 0,
      cacheHitTokens: 0,
      cacheMissTokens: 0,
    },
    citations: [],
    runs: null,
    error: null,
  };
}

const baseRequest = {
  conversationId: "c1",
  rootId: "r1",
  messageId: "m-asst",
  decision: "continue" as const,
  note: "",
  selected: [],
  userMessage: "原始问题",
  userMessageId: "u-orig",
};

let onEventCb: ((push: EventPush) => void) | null;
let resumeMock: ReturnType<typeof vi.fn>;
let startTurnMock: ReturnType<typeof vi.fn>;
let cancelMock: ReturnType<typeof vi.fn>;
let flushTurnMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetSidecarEventPumpForTests();
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useAuthStore.setState({
    status: "unauthenticated",
    user: null,
    reason: null,
  });
  dispatchSSEEventMock.mockReset();
  notifyWarningMock.mockReset();
  getConversationsMock.mockReset();
  getConversationsMock.mockReturnValue([]);
  getFoldersMock.mockReset();
  getFoldersMock.mockReturnValue([]);
  takeRecentSidecarFailureMock.mockReturnValue(null);
  // Default: mintable token — turns require one before startTurn/resume RPC.
  // No-token / remint cases override per-test.
  resolveSidecarInferenceMock.mockReset();
  resolveSidecarInferenceMock.mockResolvedValue({
    baseUrl: "https://x/v1/inference/v1",
    apiKey: "tok",
    model: "deepseek-v4-flash",
  });
  clearSidecarInferenceMock.mockReset();
  looksLikeInferenceTokenFailureMock.mockReset();
  looksLikeInferenceTokenFailureMock.mockReturnValue(false);
  resolveSidecarFoldersAuthMock.mockReset();
  resolveSidecarFoldersAuthMock.mockResolvedValue(null);
  looksLikeFoldersTokenFailureMock.mockReset();
  looksLikeFoldersTokenFailureMock.mockReturnValue(false);
  resolveSidecarAccountAuthMock.mockReset();
  resolveSidecarAccountAuthMock.mockResolvedValue(null);
  looksLikeAccountTokenFailureMock.mockReset();
  looksLikeAccountTokenFailureMock.mockReturnValue(false);
  fetchChatContextMock.mockReset();
  fetchChatContextMock.mockResolvedValue([]);

  onEventCb = null;
  resumeMock = vi.fn();
  startTurnMock = vi.fn();
  cancelMock = vi.fn(() => Promise.resolve());
  flushTurnMock = vi.fn(
    async (): Promise<OutboxFlushTurnResult> => ({
      ok: true,
      synced: {
        conversationId: "c1",
        userMessageId: "u-orig",
        cloudUserMessageId: "real-uid",
        assistantMessageId: "m-asst",
        title: "续跑标题",
      },
    }),
  );

  // The SUT bridges to the main process via the preload `window.sidecarApi` /
  // `window.outboxApi`; the node test env has no `window`, so define them directly.
  (globalThis as Record<string, unknown>).window = {
    sidecarApi: {
      onEvent: vi.fn((cb: (push: EventPush) => void) => {
        onEventCb = cb;
        return () => {
          if (onEventCb === cb) onEventCb = null;
        };
      }),
      cancel: cancelMock,
      resume: resumeMock,
      startTurn: startTurnMock,
      respond: vi.fn(),
    },
    outboxApi: {
      flushTurn: flushTurnMock,
      flush: vi.fn(),
      status: vi.fn(async () => ({ pending: [] })),
      onSynced: vi.fn(() => () => {}),
      authRefresh: vi.fn(async () => "auth_dead" as const),
    },
  };
});

afterEach(() => {
  resetSidecarEventPumpForTests();
  (globalThis as Record<string, unknown>).window = undefined;
});

/** Seed the user bubble that was pinned on pause write-back (same id end-to-end). */
function seedOriginalUserBubble(
  conversationId: string,
  userMessageId: string,
  content: string,
): void {
  useConversationStore.getState().addMessage(
    {
      id: userMessageId,
      role: "user",
      content,
      createdAt: "",
      executionId: null,
      isStreaming: false,
    },
    conversationId,
  );
}

describe("streamConversationViaSidecar", () => {
  it("mints inference with conversationId on startTurn", async () => {
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarInferenceMock).toHaveBeenCalledWith({
      conversationId: "c1",
    });
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        userMessageId: "u-opt",
        messageId: expect.stringMatching(
          /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
        ),
        traceId: expect.stringMatching(/^[0-9a-f]{32}$/i),
      }),
    );
  });

  it("remints inference with force + conversationId when pre-event token fails", async () => {
    resolveSidecarInferenceMock
      .mockResolvedValueOnce({
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "stale",
        model: "m1",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "fresh",
        model: "m1",
      });
    looksLikeInferenceTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock
      .mockRejectedValueOnce(new Error("inference token expired"))
      .mockResolvedValueOnce(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarInferenceMock).toHaveBeenNthCalledWith(1, {
      conversationId: "c1",
    });
    expect(resolveSidecarInferenceMock).toHaveBeenNthCalledWith(2, {
      force: true,
      conversationId: "c1",
    });
    expect(startTurnMock).toHaveBeenCalledTimes(2);
  });

  it("does not startTurn when no inference token even after force remint (INFERENCE_TOKEN_EXPIRED)", async () => {
    resolveSidecarInferenceMock.mockResolvedValue(null);
    seedOriginalUserBubble("c1", "u-opt", "你好");

    const err = await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).code).toBe("INFERENCE_TOKEN_EXPIRED");
    expect((err as StreamError).recoverable).toBe(false);
    expect(clearSidecarInferenceMock).toHaveBeenCalled();
    expect(resolveSidecarInferenceMock).toHaveBeenCalledWith({
      force: true,
      conversationId: "c1",
    });
    expect(startTurnMock).not.toHaveBeenCalled();
    expect(flushTurnMock).not.toHaveBeenCalled();
  });

  it("force-remints once then startTurn when initial inference mint returns null", async () => {
    resolveSidecarInferenceMock
      .mockResolvedValueOnce(null)
      .mockResolvedValueOnce({
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "fresh-after-remint",
        model: "m1",
      });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(clearSidecarInferenceMock).toHaveBeenCalled();
    expect(resolveSidecarInferenceMock).toHaveBeenNthCalledWith(2, {
      force: true,
      conversationId: "c1",
    });
    expect(startTurnMock).toHaveBeenCalledTimes(1);
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        inference: {
          baseUrl: "https://x/v1/inference/v1",
          apiKey: "fresh-after-remint",
          model: "m1",
        },
      }),
    );
  });

  it("remints folders with force when pre-event token fails", async () => {
    resolveSidecarFoldersAuthMock
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/folders",
        apiKey: "stale-folders",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/folders",
        apiKey: "fresh-folders",
      });
    looksLikeFoldersTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock
      .mockRejectedValueOnce(new Error("folders list unauthorized (401)"))
      .mockResolvedValueOnce(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarFoldersAuthMock).toHaveBeenNthCalledWith(1);
    expect(resolveSidecarFoldersAuthMock).toHaveBeenNthCalledWith(2, {
      force: true,
    });
    expect(startTurnMock).toHaveBeenCalledTimes(2);
    expect(startTurnMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        foldersAuth: {
          baseUrl: "https://api.test.example/v1/folders",
          apiKey: "fresh-folders",
        },
      }),
    );
  });

  it("remints account with force when pre-event token fails", async () => {
    resolveSidecarAccountAuthMock
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/account",
        apiKey: "stale-account",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/account",
        apiKey: "fresh-account",
      });
    looksLikeAccountTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock
      .mockRejectedValueOnce(new Error("account search unauthorized (401)"))
      .mockResolvedValueOnce(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarAccountAuthMock).toHaveBeenNthCalledWith(1);
    expect(resolveSidecarAccountAuthMock).toHaveBeenNthCalledWith(2, {
      force: true,
    });
    expect(startTurnMock).toHaveBeenCalledTimes(2);
    expect(startTurnMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "fresh-account",
        },
      }),
    );
  });

  it("forwards foldersAuth on startTurn when mint succeeds", async () => {
    resolveSidecarFoldersAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example",
      apiKey: "folders-jwt",
    });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarFoldersAuthMock).toHaveBeenCalledWith();
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        foldersAuth: {
          baseUrl: "https://api.test.example",
          apiKey: "folders-jwt",
        },
      }),
    );
  });

  it("omits foldersAuth on startTurn when mint fails (undefined, no fake success)", async () => {
    resolveSidecarFoldersAuthMock.mockResolvedValue(null);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ foldersAuth: undefined }),
    );
  });

  it("forwards accountAuth on startTurn when mint succeeds", async () => {
    resolveSidecarAccountAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-jwt",
    });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(resolveSidecarAccountAuthMock).toHaveBeenCalledWith();
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "account-jwt",
        },
      }),
    );
  });

  it("omits accountAuth on startTurn when mint fails (undefined, no fake success)", async () => {
    resolveSidecarAccountAuthMock.mockResolvedValue(null);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ accountAuth: undefined }),
    );
  });

  it("forwards foldersAuth on resume when mint succeeds", async () => {
    resolveSidecarFoldersAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example",
      apiKey: "folders-resume",
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    const result = turnResult();
    resumeMock.mockImplementation(async () => result);

    await resumeConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      messageId: "m-asst",
      decision: "continue",
      note: "",
      selected: [],
      userMessage: "原始问题",
      userMessageId: "u-orig",
    });

    expect(resolveSidecarFoldersAuthMock).toHaveBeenCalledWith();
    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        foldersAuth: {
          baseUrl: "https://api.test.example",
          apiKey: "folders-resume",
        },
      }),
    );
  });

  it("forwards accountAuth on resume when mint succeeds", async () => {
    resolveSidecarAccountAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-resume",
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    const result = turnResult();
    resumeMock.mockImplementation(async () => result);

    await resumeConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      messageId: "m-asst",
      decision: "continue",
      note: "",
      selected: [],
      userMessage: "原始问题",
      userMessageId: "u-orig",
    });

    expect(resolveSidecarAccountAuthMock).toHaveBeenCalledWith();
    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "account-resume",
        },
      }),
    );
  });

  it("forwards the logged-in account userId on startTurn (not hardcoded local)", async () => {
    useAuthStore.setState({
      status: "authenticated",
      user: {
        id: "acct-uuid-42",
        username: "alice",
        displayName: "Alice",
        email: null,
        emailVerifiedAt: null,
        role: "user",
        avatarUrl: null,
      },
      reason: null,
    });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "acct-uuid-42" }),
    );
  });

  it("falls back to local userId on startTurn when unauthenticated", async () => {
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "local" }),
    );
  });

  it("forwards conversation.folderId on startTurn when the chat belongs to a project", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: "fold-proj-1" } as never,
    ]);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ folderId: "fold-proj-1" }),
    );
  });

  it("forwards folderId: null on startTurn for bare chat (no project)", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: null } as never,
    ]);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        folderId: null,
        localRootId: null,
        localSubpath: null,
      }),
    );
  });

  it("forwards FolderMeta local binding on startTurn for a local project", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: "fold-local-1" } as never,
    ]);
    getFoldersMock.mockReturnValue([
      {
        id: "fold-local-1",
        name: "本地项目",
        mode: "local",
        localRootId: "root-abc",
        localSubpath: "apps/web",
      },
    ]);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "root-abc",
      subpath: "apps/web",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        folderId: "fold-local-1",
        localRootId: "root-abc",
        localSubpath: "apps/web",
      }),
    );
  });

  it("forwards null local binding on startTurn for a cloud project", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: "fold-cloud-1" } as never,
    ]);
    getFoldersMock.mockReturnValue([
      {
        id: "fold-cloud-1",
        name: "云项目",
        mode: "cloud",
        localRootId: null,
        localSubpath: null,
      },
    ]);
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        folderId: "fold-cloud-1",
        localRootId: null,
        localSubpath: null,
      }),
    );
  });

  it("forwards agentMentions on startTurn and flushes outbox writeback", async () => {
    const mentions = [{ agent_id: "a1", role: "研究员" }];
    seedOriginalUserBubble("c1", "u-opt", "你好 @研究员");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好 @研究员",
      optimisticUserId: "u-opt",
      history: [],
      agentMentions: mentions,
    });

    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ agentMentions: mentions }),
    );
    expect(flushTurnMock).toHaveBeenCalledWith({ userMessageId: "u-opt" });
  });

  it("reports turnCommit after a successful outbox flush", async () => {
    const turnCommit = { committed: false };
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
      turnCommit,
    });

    expect(turnCommit.committed).toBe(true);
  });

  it("passes cookie chat-context as history fallback even when accountAuth is present", async () => {
    fetchChatContextMock.mockResolvedValue([
      { role: "user", content: "先前问" },
      { role: "assistant", content: "先前答" },
    ]);
    resolveSidecarAccountAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-jwt",
    });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
    });

    expect(fetchChatContextMock).toHaveBeenCalledTimes(1);
    expect(fetchChatContextMock).toHaveBeenCalledWith("c1");
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "account-jwt",
        },
        history: [
          { role: "user", content: "先前问" },
          { role: "assistant", content: "先前答" },
        ],
      }),
    );
  });

  it("does not refetch chat-context when caller already confirmed history", async () => {
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [
        { role: "user", content: "先前问" },
        { role: "assistant", content: "先前答" },
      ],
    });

    expect(fetchChatContextMock).not.toHaveBeenCalled();
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        history: [
          { role: "user", content: "先前问" },
          { role: "assistant", content: "先前答" },
        ],
      }),
    );
  });

  it("treats caller-confirmed empty history as the window and does not refetch", async () => {
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
    });

    expect(fetchChatContextMock).not.toHaveBeenCalled();
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({ history: [] }),
    );
  });

  it("fails the turn when cookie chat-context fails and there is no accountAuth", async () => {
    fetchChatContextMock.mockRejectedValue(new Error("cloud 503"));
    resolveSidecarAccountAuthMock.mockResolvedValue(null);
    seedOriginalUserBubble("c1", "u-opt", "你好");

    const err = await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).kind).toBe("sidecar");
    expect((err as StreamError).serverMessage).toBe(
      CHAT_CONTEXT_UNAVAILABLE_MESSAGE,
    );
    expect((err as StreamError).recoverable).toBe(false);
    expect(startTurnMock).not.toHaveBeenCalled();
  });

  it("omits history when cookie chat-context fails but accountAuth can still fetch", async () => {
    fetchChatContextMock.mockRejectedValue(new Error("cloud 503"));
    resolveSidecarAccountAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-jwt",
    });
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
    });

    expect(fetchChatContextMock).toHaveBeenCalledTimes(1);
    expect(startTurnMock).toHaveBeenCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "account-jwt",
        },
        history: undefined,
      }),
    );
  });

  it("surfaces sidecar chat-context failure as a non-recoverable banner", async () => {
    resolveSidecarAccountAuthMock.mockResolvedValue({
      baseUrl: "https://api.test.example/v1/account",
      apiKey: "account-jwt",
    });
    fetchChatContextMock.mockRejectedValue(new Error("cloud 503"));
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockRejectedValue(
      new Error(
        "Error invoking remote method 'sidecar:startTurn': Error: 未能加载对话历史，请稍后重试。",
      ),
    );

    const err = await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
    }).catch((e: unknown) => e);

    expect(err).toBeInstanceOf(StreamError);
    const se = err as StreamError;
    expect(se.kind).toBe("sidecar");
    expect(se.recoverable).toBe(false);
    expect(se.serverMessage).toContain("未能加载对话历史");
    expect(startTurnMock).toHaveBeenCalledTimes(1);
  });

  it("does not report turnCommit when outbox flush is still pending", async () => {
    flushTurnMock.mockResolvedValue({
      ok: false,
      error: "writeback_pending",
    });
    const turnCommit = { committed: false };
    seedOriginalUserBubble("c1", "u-opt", "你好");
    startTurnMock.mockResolvedValue(turnResult());

    await streamConversationViaSidecar({
      conversationId: "c1",
      rootId: "r1",
      content: "你好",
      optimisticUserId: "u-opt",
      history: [],
      turnCommit,
    });

    expect(turnCommit.committed).toBe(false);
  });
});

describe("resumeConversationViaSidecar", () => {
  it("mints inference with conversationId on resume", async () => {
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    expect(resolveSidecarInferenceMock).toHaveBeenCalledWith({
      conversationId: "c1",
    });
  });

  it("remints inference with force + conversationId when pre-event token fails on resume", async () => {
    resolveSidecarInferenceMock
      .mockResolvedValueOnce({
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "stale",
        model: "m1",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://x/v1/inference/v1",
        apiKey: "fresh",
        model: "m1",
      });
    looksLikeInferenceTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock
      .mockRejectedValueOnce(new Error("inference token expired"))
      .mockResolvedValueOnce(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    expect(resolveSidecarInferenceMock).toHaveBeenNthCalledWith(1, {
      conversationId: "c1",
    });
    expect(resolveSidecarInferenceMock).toHaveBeenNthCalledWith(2, {
      force: true,
      conversationId: "c1",
    });
    expect(resumeMock).toHaveBeenCalledTimes(2);
  });

  it("does not resume when no inference token even after force remint (INFERENCE_TOKEN_EXPIRED)", async () => {
    resolveSidecarInferenceMock.mockResolvedValue(null);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");

    const err = await resumeConversationViaSidecar(baseRequest).catch(
      (e: unknown) => e,
    );

    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).code).toBe("INFERENCE_TOKEN_EXPIRED");
    expect((err as StreamError).recoverable).toBe(false);
    expect(clearSidecarInferenceMock).toHaveBeenCalled();
    expect(resolveSidecarInferenceMock).toHaveBeenCalledWith({
      force: true,
      conversationId: "c1",
    });
    expect(resumeMock).not.toHaveBeenCalled();
    expect(flushTurnMock).not.toHaveBeenCalled();
  });

  it("remints folders with force when pre-event token fails on resume", async () => {
    resolveSidecarFoldersAuthMock
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/folders",
        apiKey: "stale-folders",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/folders",
        apiKey: "fresh-folders",
      });
    looksLikeFoldersTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock
      .mockRejectedValueOnce(new Error("folders list unauthorized (401)"))
      .mockResolvedValueOnce(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    expect(resolveSidecarFoldersAuthMock).toHaveBeenNthCalledWith(1);
    expect(resolveSidecarFoldersAuthMock).toHaveBeenNthCalledWith(2, {
      force: true,
    });
    expect(resumeMock).toHaveBeenCalledTimes(2);
    expect(resumeMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        foldersAuth: {
          baseUrl: "https://api.test.example/v1/folders",
          apiKey: "fresh-folders",
        },
      }),
    );
  });

  it("remints account with force when pre-event token fails on resume", async () => {
    resolveSidecarAccountAuthMock
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/account",
        apiKey: "stale-account",
      })
      .mockResolvedValueOnce({
        baseUrl: "https://api.test.example/v1/account",
        apiKey: "fresh-account",
      });
    looksLikeAccountTokenFailureMock.mockReturnValue(true);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock
      .mockRejectedValueOnce(new Error("account search unauthorized (401)"))
      .mockResolvedValueOnce(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    expect(resolveSidecarAccountAuthMock).toHaveBeenNthCalledWith(1);
    expect(resolveSidecarAccountAuthMock).toHaveBeenNthCalledWith(2, {
      force: true,
    });
    expect(resumeMock).toHaveBeenCalledTimes(2);
    expect(resumeMock).toHaveBeenLastCalledWith(
      expect.objectContaining({
        accountAuth: {
          baseUrl: "https://api.test.example/v1/account",
          apiKey: "fresh-account",
        },
      }),
    );
  });

  it("forwards project folderId + local binding on resume (symmetric with startTurn)", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: "fold-local-1" } as never,
    ]);
    getFoldersMock.mockReturnValue([
      {
        id: "fold-local-1",
        name: "本地项目",
        mode: "local",
        localRootId: "root-abc",
        localSubpath: "",
      },
    ]);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue(turnResult());

    await resumeConversationViaSidecar({
      ...baseRequest,
      rootId: "root-abc",
      subpath: "",
    });

    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        folderId: "fold-local-1",
        localRootId: "root-abc",
        localSubpath: "",
      }),
    );
  });

  it("forwards folderId: null on resume for bare chat (covers frame ownership)", async () => {
    getConversationsMock.mockReturnValue([
      { id: "c1", folderId: null } as never,
    ]);
    getFoldersMock.mockReturnValue([]);
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue(turnResult());

    await resumeConversationViaSidecar({
      ...baseRequest,
      rootId: "root-bare",
      subpath: "",
    });

    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        folderId: null,
        localRootId: null,
        localSubpath: null,
      }),
    );
  });

  it("drives the resume RPC, forwards only this conversation's events, and reconciles the original user bubble on outbox sync", async () => {
    seedOriginalUserBubble("c1", "u-orig", "原始问题");

    const result = turnResult();
    // `resume` runs once the SUT reaches `await invoke()` — by then onEvent is
    // subscribed, so the engine "streams" one matching + one foreign event.
    resumeMock.mockImplementation(async () => {
      onEventCb?.({
        conversationId: "c1",
        turnId: "m-asst",
        event: { type: "content_delta", payload: { delta: "x" } },
      });
      onEventCb?.({
        conversationId: "other",
        turnId: "m-asst",
        event: { type: "content_delta", payload: { delta: "y" } },
      });
      return result;
    });

    await expect(resumeConversationViaSidecar(baseRequest)).resolves.toBe(
      result,
    );

    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        rootId: "r1",
        conversationId: "c1",
        messageId: "m-asst",
        decision: "continue",
        note: "",
        selected: [],
        subpath: undefined,
        inference: expect.objectContaining({
          apiKey: "tok",
          model: "deepseek-v4-flash",
        }),
        userId: "local",
        traceId: expect.any(String),
      }),
    );
    // Foreign-conversation event filtered out; only c1's reached the dispatcher.
    expect(dispatchSSEEventMock).toHaveBeenCalledTimes(1);

    // Outbox flush is keyed on the original user bubble id (pause write-back).
    expect(flushTurnMock).toHaveBeenCalledWith({ userMessageId: "u-orig" });

    // The original bubble's id is swapped for the authoritative one when unchanged.
    const userMsg = useConversationStore
      .getState()
      .byId.c1?.messages.find((m) => m.role === "user");
    expect(userMsg?.id).toBe("real-uid");
  });

  it("forwards the logged-in account userId on resume (not hardcoded local)", async () => {
    useAuthStore.setState({
      status: "authenticated",
      user: {
        id: "acct-uuid-42",
        username: "alice",
        displayName: "Alice",
        email: null,
        emailVerifiedAt: null,
        role: "user",
        avatarUrl: null,
      },
      reason: null,
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({ userId: "acct-uuid-42" }),
    );
  });

  it("completes a turn without platform-fallback warning", async () => {
    flushTurnMock.mockResolvedValue({
      ok: true,
      synced: {
        conversationId: "c1",
        userMessageId: "u-orig",
        cloudUserMessageId: "real-uid",
        assistantMessageId: null,
        title: null,
      },
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue({ ...turnResult(), model: "gpt-4o" });

    await resumeConversationViaSidecar(baseRequest);

    expect(notifyWarningMock).not.toHaveBeenCalled();
  });

  it("passes the account model on a normal turn (token present)", async () => {
    resolveSidecarInferenceMock.mockResolvedValue({
      baseUrl: "https://x/v1/inference/v1",
      apiKey: "tok",
      model: "deepseek-v4-flash",
    });
    flushTurnMock.mockResolvedValue({
      ok: true,
      synced: {
        conversationId: "c1",
        userMessageId: "u-orig",
        cloudUserMessageId: "real-uid",
        assistantMessageId: null,
        title: null,
      },
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue({
      ...turnResult(),
      model: "deepseek-v4-flash",
    });

    await resumeConversationViaSidecar(baseRequest);

    expect(resumeMock).toHaveBeenCalledWith(
      expect.objectContaining({
        inference: expect.objectContaining({ model: "deepseek-v4-flash" }),
      }),
    );
    expect(notifyWarningMock).not.toHaveBeenCalled();
  });

  it("keeps synced_pending when outbox flush is still pending", async () => {
    flushTurnMock.mockResolvedValue({
      ok: false,
      error: "writeback_pending",
    });
    seedOriginalUserBubble("c1", "u-orig", "原始问题");
    resumeMock.mockResolvedValue(turnResult());

    await resumeConversationViaSidecar(baseRequest);

    const userMsg = useConversationStore
      .getState()
      .byId.c1?.messages.find((m) => m.role === "user");
    expect(userMsg?.syncStatus).toBe("synced_pending");
    // No toast / manual-retry path (as-built: 双模式工作区 §10.3; 前端 UX §一B).
    expect(notifyWarningMock).not.toHaveBeenCalled();
  });

  it("maps a resume failure to a sidecar StreamError carrying the engine's reason", async () => {
    resumeMock.mockRejectedValue(new Error("引擎崩了"));

    const err = await resumeConversationViaSidecar(baseRequest).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).kind).toBe("sidecar");
    expect((err as StreamError).serverMessage).toContain("引擎崩了");
    // A turn that never completed must not flush outbox.
    expect(flushTurnMock).not.toHaveBeenCalled();
  });

  it("surfaces IPC invalid-args rejects with field-level sidecar banner copy", async () => {
    // Electron wraps main-process throws; message after unwrap matches IpcInvalidArgsError.
    resumeMock.mockRejectedValue(
      new Error(
        "Error invoking remote method 'sidecar:resume': Error: 无效的 IPC 入参：sidecar:resume（字段 permissionAxes 期望 string）",
      ),
    );

    const err = await resumeConversationViaSidecar(baseRequest).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(StreamError);
    expect((err as StreamError).kind).toBe("sidecar");
    expect((err as StreamError).serverMessage).toBe(
      "本地引擎出错：请求参数校验失败（permissionAxes 期望 string，sidecar:resume）",
    );
  });

  it("maps turn already running to concurrency copy (not 本地引擎出错)", async () => {
    takeRecentSidecarFailureMock.mockReturnValue(
      "找不到 Python，无法启动本地引擎",
    );
    resumeMock.mockRejectedValue(
      new Error(
        "Error invoking remote method 'sidecar:resume': Error: turn already running",
      ),
    );

    const err = await resumeConversationViaSidecar(baseRequest).catch(
      (e: unknown) => e,
    );
    expect(err).toBeInstanceOf(StreamError);
    const se = err as StreamError;
    expect(se.kind).toBe("sidecar");
    expect(se.code).toBe("sidecar_turn_busy");
    expect(se.recoverable).toBe(false);
    expect(se.serverMessage).toContain("回合在进行");
    expect(se.serverMessage).not.toContain("本地引擎出错");
    // 不得被陈旧 onStatus 诊断盖住。
    expect(se.serverMessage).not.toContain("找不到 Python");
    // 横幅走 serverMessage（非 turn_in_progress 盖文案）。
    expect(describeStreamError(se)).toBe(se.serverMessage);
  });

  it("prefers an onStatus lifecycle diagnostic over the rejection reason", async () => {
    takeRecentSidecarFailureMock.mockReturnValue(
      "找不到 Python，无法启动本地引擎",
    );
    resumeMock.mockRejectedValue(new Error("generic rpc error"));

    const err = await resumeConversationViaSidecar(baseRequest).catch(
      (e: unknown) => e,
    );
    expect((err as StreamError).serverMessage).toBe(
      "找不到 Python，无法启动本地引擎",
    );
  });

  it("does not invoke when signal is already aborted (H1 pre-aborted gate)", async () => {
    const ac = new AbortController();
    ac.abort();
    const err = await resumeConversationViaSidecar({
      ...baseRequest,
      signal: ac.signal,
    }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe("AbortError");
    expect(resumeMock).not.toHaveBeenCalled();
    expect(cancelMock).not.toHaveBeenCalled();
  });

  it("AbortSignal does not cancel the engine (viewer disconnect ≠ stop)", async () => {
    const ac = new AbortController();
    let resolveResume: (v: unknown) => void = () => {};
    resumeMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveResume = resolve;
        }),
    );

    const p = resumeConversationViaSidecar({
      ...baseRequest,
      signal: ac.signal,
    });
    p.catch(() => {});

    await vi.waitFor(() => expect(resumeMock).toHaveBeenCalled());
    ac.abort();
    // C1: abort 只影响 UI 观察门禁，不得 fire-and-forget cancel 引擎。
    expect(cancelMock).not.toHaveBeenCalled();

    resolveResume(turnResult());
    await expect(p).resolves.toEqual(turnResult());
    expect(cancelMock).not.toHaveBeenCalled();
  });

  it("maps honest stop (phase stopping, signal intact) to AbortError", async () => {
    // Production stopGeneration does NOT abort AbortSignal — it sets turnPhase
    // stopping and sidecar cancel; startTurn/resume then reject with turn cancelled.
    seedOriginalUserBubble("c1", "u-orig", "原始问题");

    let rejectResume: (e: unknown) => void = () => {};
    resumeMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectResume = reject;
        }),
    );

    const ac = new AbortController();
    const p = resumeConversationViaSidecar({
      ...baseRequest,
      signal: ac.signal,
    });
    p.catch(() => {});

    await vi.waitFor(() => expect(resumeMock).toHaveBeenCalled());
    // Honest stop mid-flight: phase → stopping, signal stays live.
    useConversationStore.getState().setTurnPhase("stopping", "c1");
    expect(ac.signal.aborted).toBe(false);
    rejectResume(
      new Error(
        "Error invoking remote method 'sidecar:resume': Error: turn cancelled",
      ),
    );

    const err = await p.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe("AbortError");
    expect(flushTurnMock).not.toHaveBeenCalled();
  });

  it("maps turn-cancelled reject to AbortError even after message_end left phase stopped", async () => {
    // FIFO: message_end(cancelled) → completeTurnPhase(stopped) before RPC reject.
    seedOriginalUserBubble("c1", "u-orig", "原始问题");

    let rejectResume: (e: unknown) => void = () => {};
    resumeMock.mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectResume = reject;
        }),
    );

    const p = resumeConversationViaSidecar(baseRequest);
    p.catch(() => {});

    await vi.waitFor(() => expect(resumeMock).toHaveBeenCalled());
    useConversationStore.getState().setTurnPhase("stopped", "c1");
    rejectResume(new Error("turn cancelled"));

    const err = await p.catch((e: unknown) => e);
    expect(err).toBeInstanceOf(DOMException);
    expect((err as DOMException).name).toBe("AbortError");
    expect(flushTurnMock).not.toHaveBeenCalled();
  });
});
