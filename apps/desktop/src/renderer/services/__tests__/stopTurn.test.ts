import { api } from "@/services/api";
import {
  clearActiveSidecarTurn,
  resetSidecarRoutingForTests,
  setActiveSidecarTurn,
} from "@/services/sidecarRouting";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { stopConversation } from "../stopTurn";

vi.mock("@/services/api", () => ({ api: { post: vi.fn() } }));

const post = vi.mocked(api.post);
const CID = "conv-1";

let cancelMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  resetSidecarRoutingForTests();
  post.mockReset();
  post.mockResolvedValue({ stopped: true });
  cancelMock = vi.fn().mockResolvedValue(undefined);
  (globalThis as Record<string, unknown>).window = {
    sidecarApi: { cancel: cancelMock },
  };
});

afterEach(() => {
  resetSidecarRoutingForTests();
});

describe("stopConversation", () => {
  it("cloud turn: POSTs /stop and returns stopped", async () => {
    await expect(stopConversation(CID)).resolves.toBe(true);
    expect(cancelMock).not.toHaveBeenCalled();
    expect(post).toHaveBeenCalledWith("/v1/conversations/conv-1/stop");
  });

  it("cloud turn: propagates POST failures", async () => {
    post.mockRejectedValueOnce(new Error("boom"));
    await expect(stopConversation(CID)).rejects.toThrow("boom");
  });

  it("sidecar turn: routes to sidecarApi.cancel with user_stop (never cloud POST)", async () => {
    setActiveSidecarTurn(CID, "root-9", "scratch/c1", "turn-42");

    await expect(stopConversation(CID)).resolves.toBe(true);

    expect(post).not.toHaveBeenCalled();
    expect(cancelMock).toHaveBeenCalledWith({
      rootId: "root-9",
      subpath: "scratch/c1",
      turnId: "turn-42",
      conversationId: CID,
      reason: "user_stop",
    });
  });

  it("still cancels sidecar after the live map is cleared (last turnId)", async () => {
    setActiveSidecarTurn(CID, "root-9", "scratch/c1", "turn-42");
    clearActiveSidecarTurn(CID);

    await expect(stopConversation(CID)).resolves.toBe(true);
    expect(cancelMock).toHaveBeenCalledWith(
      expect.objectContaining({ turnId: "turn-42", rootId: "root-9" }),
    );
    expect(post).not.toHaveBeenCalled();
  });

  it("sidecar turn: surfaces cancel failures for retry UI", async () => {
    setActiveSidecarTurn(CID, "root-9", "", "turn-42");
    cancelMock.mockRejectedValueOnce(new Error("本地引擎未运行，无法停止"));

    await expect(stopConversation(CID)).rejects.toThrow(/无法停止/);
    expect(post).not.toHaveBeenCalled();
  });

  it("sidecar turn without turnId: throws instead of silent no-op", async () => {
    setActiveSidecarTurn(CID, "root-9", "");
    await expect(stopConversation(CID)).rejects.toThrow(/标识缺失/);
    expect(cancelMock).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });
});
