// @vitest-environment jsdom
import { api } from "@/services/api";
import {
  clearActiveSidecarTurn,
  resetSidecarRoutingForTests,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { submitRunStop } from "../runStop";

vi.mock("@/services/api", () => ({ api: { post: vi.fn() } }));

const post = vi.mocked(api.post);
const CID = "conv-1";

let runStop: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetSidecarRoutingForTests();
  post.mockReset();
  runStop = vi.fn().mockResolvedValue({ queued: 2, accepted: true });
  window.sidecarApi = { ...window.sidecarApi, runStop };
});

afterEach(() => {
  resetSidecarRoutingForTests();
});

describe("submitRunStop", () => {
  it("posts snake_case body to the run-stop endpoint (one worker)", async () => {
    post.mockResolvedValue({ queued: 1 });

    const out = await submitRunStop(CID, {
      executionId: "exec-1",
      runId: "r1",
    });

    expect(out).toEqual({ queued: 1 });
    expect(post).toHaveBeenCalledWith("/v1/conversations/conv-1/run-stop", {
      execution_id: "exec-1",
      run_id: "r1",
    });
    expect(runStop).not.toHaveBeenCalled();
  });

  it("omits run scope as null when stopping the whole execution", async () => {
    post.mockResolvedValue({ queued: 3 });

    await submitRunStop(CID, { executionId: "exec-1" });

    expect(post).toHaveBeenCalledWith("/v1/conversations/conv-1/run-stop", {
      execution_id: "exec-1",
      run_id: null,
    });
  });

  it("routes local turns to sidecarApi.runStop", async () => {
    setActiveSidecarTurn(CID, "root-1", "conversations/conv-1", "turn-1");

    const out = await submitRunStop(CID, {
      executionId: "exec-1",
      runId: "r2",
    });

    expect(out).toEqual({ queued: 2, accepted: true });
    expect(runStop).toHaveBeenCalledWith({
      rootId: "root-1",
      subpath: "conversations/conv-1",
      conversationId: CID,
      executionId: "exec-1",
      runId: "r2",
    });
    expect(post).not.toHaveBeenCalled();
  });

  it("still routes to sidecar after the live map is cleared (last target)", async () => {
    setActiveSidecarTurn(CID, "root-1", "conversations/conv-1", "turn-1");
    clearActiveSidecarTurn(CID);

    await submitRunStop(CID, { executionId: "exec-1" });

    expect(runStop).toHaveBeenCalledWith(
      expect.objectContaining({
        rootId: "root-1",
        subpath: "conversations/conv-1",
        conversationId: CID,
        runId: null,
      }),
    );
    expect(post).not.toHaveBeenCalled();
  });
});
