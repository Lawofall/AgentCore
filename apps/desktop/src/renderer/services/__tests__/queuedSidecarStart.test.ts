import { testAssemblyProfile } from "@/lib/assemblyCapability";
import { queryClient } from "@/lib/queryClient";
import { conversationKeys, llmModelProfileKeys } from "@/lib/queryKeys";
import * as accountToken from "@/services/accountToken";
import * as foldersToken from "@/services/foldersToken";
import * as inferenceToken from "@/services/inferenceToken";
import * as permissionAxes from "@/services/permissionAxes";
import { resetSidecarEventPumpForTests } from "@/services/sidecarEventPump";
import {
  resetQueuedSidecarStartsForTests,
  startQueuedSidecarTurn,
} from "@/services/streamConversationViaSidecar";
import { resetStreamOwnershipForTests } from "@/services/turns/streamOwnership";
import * as workspacesToken from "@/services/workspacesToken";
import { getRuntime, useConversationStore } from "@/stores/conversation";
import type { SidecarEventPush } from "@shared/sidecar-contract";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const inference = {
  baseUrl: "http://127.0.0.1/v1/inference/v1",
  apiKey: "tok",
  model: "m",
};

const CID = "conv-queue-start";

let onEventCb: ((push: SidecarEventPush) => void) | null = null;

beforeEach(() => {
  resetSidecarEventPumpForTests();
  resetStreamOwnershipForTests();
  vi.spyOn(inferenceToken, "resolveSidecarInference").mockResolvedValue(
    inference,
  );
  vi.spyOn(foldersToken, "resolveSidecarFoldersAuth").mockResolvedValue(null);
  vi.spyOn(accountToken, "resolveSidecarAccountAuth").mockResolvedValue(null);
  vi.spyOn(workspacesToken, "resolveSidecarWorkspacesAuth").mockResolvedValue(
    null,
  );
  vi.spyOn(
    permissionAxes,
    "resolveConversationPermissionAxes",
  ).mockResolvedValue({ boundary: "folder" });
  queryClient.removeQueries({ queryKey: conversationKeys.grouped });
  const profile = testAssemblyProfile();
  queryClient.setQueryData(llmModelProfileKeys.list, {
    data: [profile],
    default_assembly_id: profile.id,
  });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  useConversationStore.getState().switchConversation(CID);
  useConversationStore.getState().setTurnPhase("completed", CID);
  onEventCb = null;
  vi.stubGlobal("window", {
    sidecarApi: {
      onEvent: (cb: (push: SidecarEventPush) => void) => {
        onEventCb = cb;
        return () => {
          if (onEventCb === cb) onEventCb = null;
        };
      },
      startTurn: vi.fn(async (req: { turnId: string }) => {
        expect(getRuntime(CID).messages.some((m) => m.role === "user")).toBe(
          false,
        );
        onEventCb?.({
          conversationId: CID,
          turnId: req.turnId,
          event: {
            type: "turn_queue_started",
            timestamp: "",
            payload: {
              queue_id: "q1",
              conversation_id: CID,
              remaining_depth: 0,
              content: "好的",
              user_message_id: "u-queued",
            },
          },
        });
        return {
          turnId: req.turnId,
          messageId: "m-asst",
          content: "",
          reasoningContent: null,
          finishReason: "stop",
          model: "m",
          rounds: 1,
          usage: {
            inputTokens: 0,
            outputTokens: 0,
            reasoningTokens: 0,
            cacheHitTokens: 0,
            cacheMissTokens: 0,
          },
        };
      }),
      cancel: vi.fn(async () => {}),
    },
    outboxApi: {
      flushTurn: vi.fn(async () => ({ ok: false, error: "test" })),
    },
  });
});

afterEach(() => {
  resetQueuedSidecarStartsForTests();
  resetSidecarEventPumpForTests();
  resetStreamOwnershipForTests();
  queryClient.removeQueries({ queryKey: llmModelProfileKeys.list });
  useConversationStore.setState({ currentConversationId: null, byId: {} });
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("startQueuedSidecarTurn", () => {
  it("上一回合已结束也能开跑，用户行等出队帧才出现", async () => {
    await startQueuedSidecarTurn({
      conversationId: CID,
      rootId: "r1",
      subpath: "",
      queueId: "q1",
      userMessageId: "u-queued",
      messageId: "m-asst",
      traceId: "a".repeat(32),
      userMessage: "好的",
    });

    const startTurn = vi.mocked(window.sidecarApi.startTurn);
    expect(startTurn).toHaveBeenCalledWith(
      expect.objectContaining({
        conversationId: CID,
        queueId: "q1",
        userMessageId: "u-queued",
        userMessage: "好的",
        inference,
        session: expect.objectContaining({
          folderId: null,
          localRootId: null,
          localSubpath: null,
        }),
      }),
    );
    expect(getRuntime(CID).messages.filter((m) => m.role === "user")).toEqual([
      expect.objectContaining({ id: "u-queued", content: "好的" }),
    ]);
  });

  it("项目会话出队带上文件夹绑定", async () => {
    queryClient.setQueryData(conversationKeys.grouped, {
      folders: [{ id: "f1", localRootId: "root-a", localSubpath: "src" }],
      conversations: [{ id: CID, folderId: "f1" }],
    });
    await startQueuedSidecarTurn({
      conversationId: CID,
      rootId: "r1",
      subpath: "",
      queueId: "q1",
      userMessageId: "u-queued",
      messageId: "m-asst",
      traceId: "a".repeat(32),
      userMessage: "好的",
    });
    expect(vi.mocked(window.sidecarApi.startTurn)).toHaveBeenCalledWith(
      expect.objectContaining({
        session: expect.objectContaining({
          folderId: "f1",
          localRootId: "root-a",
          localSubpath: "src",
        }),
      }),
    );
  });
});
