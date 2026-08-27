import { EXECUTION_HARVEST_ORIGIN } from "@/lib/executionHarvest";
import { useConversationStore } from "@/stores/conversation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const loadLatestWindow = vi.fn(
  async (_conversationId: string, _opts?: { softRefresh?: boolean }) => true,
);
vi.mock("@/services/messages", () => ({
  loadLatestWindow: (
    conversationId: string,
    opts?: { softRefresh?: boolean },
  ) => loadLatestWindow(conversationId, opts),
}));

vi.mock("@/hooks/useConversations", () => ({
  patchConversationCache: vi.fn(),
}));

import { applyOutboxSynced } from "../outboxReconcile";
import {
  beginLocalConversationStream,
  resetStreamOwnershipForTests,
} from "../turns/streamOwnership";

const CID = "c-harvest";

function harvestAck(
  over: Partial<Parameters<typeof applyOutboxSynced>[0]> = {},
) {
  return {
    conversationId: CID,
    userMessageId: "u-harvest",
    cloudUserMessageId: "u-harvest",
    assistantMessageId: "m-harvest",
    title: null,
    origin: EXECUTION_HARVEST_ORIGIN,
    harvestKind: "completed",
    ...over,
  };
}

beforeEach(() => {
  vi.useFakeTimers();
  loadLatestWindow.mockClear();
  resetStreamOwnershipForTests();
  useConversationStore.setState({
    currentConversationId: CID,
    byId: {},
    sliceLruOrder: [],
    pendingFocus: null,
  });
});

afterEach(() => {
  vi.useRealTimers();
  resetStreamOwnershipForTests();
});

describe("applyOutboxSynced write-back", () => {
  it("does not extra-refresh after leftover harvest write-back", () => {
    applyOutboxSynced(harvestAck());
    expect(loadLatestWindow).not.toHaveBeenCalled();
  });

  it("does not extra-refresh while a later local stream is live", () => {
    const release = beginLocalConversationStream(CID);
    applyOutboxSynced(harvestAck());
    expect(loadLatestWindow).not.toHaveBeenCalled();
    release();
    expect(loadLatestWindow).not.toHaveBeenCalled();
  });

  it("ordinary write-back does not softRefresh", () => {
    applyOutboxSynced(
      harvestAck({
        userMessageId: "u-user",
        cloudUserMessageId: "u-user",
        origin: null,
        harvestKind: null,
      }),
    );
    expect(loadLatestWindow).not.toHaveBeenCalled();
  });
});
