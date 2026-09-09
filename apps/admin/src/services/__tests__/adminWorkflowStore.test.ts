import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearCsrfToken } from "../api";
import { takedownWorkflowStoreListing } from "../adminWorkflowStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  clearCsrfToken();
});

describe("adminWorkflowStore", () => {
  it("POSTs takedown to /v1/admin/workflow-store/listings/{id}/takedown", async () => {
    const sent: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init: RequestInit) => {
        sent.push({ url, init });
        return Promise.resolve(
          new Response(
            JSON.stringify({
              author: "作者甲",
              author_user_id: "u-author",
              description: "",
              id: "lst-1",
              name: "周报流水线",
              status: "taken_down",
              updated_at: "2026-09-02T00:00:00Z",
              version_n: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );

    await takedownWorkflowStoreListing("lst-1");

    expect(sent[0]?.url).toContain(
      "/v1/admin/workflow-store/listings/lst-1/takedown",
    );
    expect(sent[0]?.init.method).toBe("POST");
  });
});
