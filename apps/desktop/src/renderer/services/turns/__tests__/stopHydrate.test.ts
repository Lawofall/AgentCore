import { logEvent } from "@/lib/log";
import { loadLatestWindow } from "@/services/messages";
import {
  STOP_HYDRATE_MS,
  armStopHydrateWatchdog,
  resetStopHydrateWatchdogForTests,
} from "@/services/turns/stopHydrate";
import {
  beginTurnPreflight,
  enterTurnStreaming,
  getRuntime,
  getTurnPhase,
  useConversationStore,
} from "@/stores/conversation";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/services/messages", () => ({
  loadLatestWindow: vi.fn(async () => true),
}));

vi.mock("@/lib/log", () => ({
  logEvent: vi.fn(),
}));

const CID = "conv-stop-hydrate";
const loadLatest = vi.mocked(loadLatestWindow);
const log = vi.mocked(logEvent);

beforeEach(() => {
  vi.useFakeTimers();
  loadLatest.mockClear();
  log.mockClear();
  useConversationStore.setState({ currentConversationId: CID, byId: {} });
  useConversationStore.getState().switchConversation(CID);
});

afterEach(() => {
  resetStopHydrateWatchdogForTests();
  vi.useRealTimers();
});

describe("stop hydrate watchdog", () => {
  it("still stopping after delay → hydrate + stamp cancelled", async () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().createAssistantMessage(CID);
    useConversationStore.getState().setTurnPhase("stopping", CID);
    expect(getRuntime(CID).isGenerating).toBe(true);

    armStopHydrateWatchdog(CID);
    expect(loadLatest).not.toHaveBeenCalled();
    expect(getTurnPhase(CID)).toBe("stopping");

    await vi.advanceTimersByTimeAsync(STOP_HYDRATE_MS);

    expect(loadLatest).toHaveBeenCalledWith(CID, { softRefresh: true });
    expect(getTurnPhase(CID)).toBe("stopped");
    expect(getRuntime(CID).isGenerating).toBe(false);
    const tail = getRuntime(CID).messages.at(-1);
    expect(tail?.finishReason).toBe("cancelled");
    expect(log).toHaveBeenCalledWith(
      "info",
      "conversation.stop_hydrate",
      expect.objectContaining({ conversation_id: CID }),
    );
  });

  it("message_end already settled → timer is a no-op", async () => {
    beginTurnPreflight(CID);
    enterTurnStreaming(CID);
    useConversationStore.getState().setTurnPhase("stopping", CID);
    armStopHydrateWatchdog(CID);
    useConversationStore.getState().setTurnPhase("stopped", CID);

    await vi.advanceTimersByTimeAsync(STOP_HYDRATE_MS);

    expect(loadLatest).not.toHaveBeenCalled();
    expect(getTurnPhase(CID)).toBe("stopped");
  });
});
