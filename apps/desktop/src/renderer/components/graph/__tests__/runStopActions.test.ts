import { useRunStopPendingStore } from "@/stores/runStopPending";
import { toast } from "sonner";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { requestRunStop } from "../runStopActions";

const convPhase = vi.hoisted(() => ({
  turnPhase: "streaming" as string,
}));

const submitRunStop = vi.fn();

vi.mock("@/services/runStop", () => ({
  submitRunStop: (...args: unknown[]) => submitRunStop(...args),
}));

vi.mock("@/stores/conversation", () => ({
  useConversationStore: {
    getState: () => ({ currentConversationId: "c1", byId: {} }),
  },
  runtimeOf: () => ({ turnPhase: convPhase.turnPhase }),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

const NO_DRIVE = {
  queued: 0,
  accepted: false,
  reason: "no_live_drive",
  detail: "这批工作已经不在引擎手里了，没有能停的在跑队员。",
};

describe("requestRunStop toast", () => {
  beforeEach(() => {
    convPhase.turnPhase = "streaming";
    submitRunStop.mockReset();
    vi.mocked(toast.success).mockReset();
    vi.mocked(toast.error).mockReset();
    vi.mocked(toast.warning).mockReset();
    vi.mocked(toast.info).mockReset();
    useRunStopPendingStore.getState().reset();
  });

  it("warns when the engine has no live drive and the turn is still live", async () => {
    submitRunStop.mockResolvedValue(NO_DRIVE);

    await requestRunStop({
      conversationId: "c1",
      executionId: "exec1",
      runId: null,
      scope: "team",
    });

    expect(toast.warning).toHaveBeenCalledWith("没有停下任何工作", {
      description: NO_DRIVE.detail,
    });
    expect(toast.info).not.toHaveBeenCalled();
  });

  it("does not warn no-work when no_live_drive lands during whole-turn stopping", async () => {
    convPhase.turnPhase = "stopping";
    submitRunStop.mockResolvedValue(NO_DRIVE);

    await requestRunStop({
      conversationId: "c1",
      executionId: "exec1",
      runId: null,
      scope: "team",
    });

    expect(toast.warning).not.toHaveBeenCalled();
    expect(toast.info).toHaveBeenCalledWith("整轮正在停下来");
  });
});
