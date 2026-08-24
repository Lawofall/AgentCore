import { api } from "@/services/api";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { acceptRunOutcome, submitRunRedirect } from "../runRedirect";

vi.mock("@/services/api", () => ({ api: { post: vi.fn() } }));
vi.mock("@/services/sidecarRouting", () => ({
  resolveSidecarControlTargetForEngine: vi.fn(async () => null),
}));
vi.mock("@/stores/conversation", () => ({
  useConversationStore: { getState: () => ({ byId: {} }) },
}));

const post = vi.mocked(api.post);

beforeEach(() => {
  post.mockReset();
});

describe("submitRunRedirect", () => {
  it("posts snake_case body to the run-redirect endpoint", async () => {
    post.mockResolvedValue({ ok: true, queued: 1 });

    await submitRunRedirect("conv-1", {
      executionId: "exec-1",
      runId: "r1",
      feedback: "改做竞品分析",
    });

    expect(post).toHaveBeenCalledWith("/v1/conversations/conv-1/run-redirect", {
      execution_id: "exec-1",
      run_id: "r1",
      feedback: "改做竞品分析",
    });
  });
});

describe("acceptRunOutcome", () => {
  it("posts to the turn-scoped accept-outcome endpoint", async () => {
    post.mockResolvedValue({
      ok: true,
      recorded: true,
      action: "run.outcome_accepted",
    });

    const out = await acceptRunOutcome("conv-1", {
      messageId: "msg-1",
      runId: "r1",
      reason: "redirect_ignored",
      executionId: "exec-1",
      note: "ok",
    });

    expect(out).toEqual({
      ok: true,
      recorded: true,
      action: "run.outcome_accepted",
    });
    expect(post).toHaveBeenCalledWith(
      "/v1/conversations/conv-1/messages/msg-1/accept-outcome",
      {
        run_id: "r1",
        reason: "redirect_ignored",
        execution_id: "exec-1",
        note: "ok",
      },
    );
  });
});
