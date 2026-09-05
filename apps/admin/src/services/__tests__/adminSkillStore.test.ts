import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { clearCsrfToken } from "../api";
import { takedownSkillStoreListing } from "../adminSkillStore";

afterEach(() => {
  vi.unstubAllGlobals();
});

beforeEach(() => {
  clearCsrfToken();
});

describe("adminSkillStore", () => {
  it("POSTs takedown to /v1/admin/skill-store/listings/{id}/takedown", async () => {
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
              name: "合同审查",
              status: "taken_down",
              updated_at: "2026-09-02T00:00:00Z",
              version_n: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );

    await takedownSkillStoreListing("lst-1");

    expect(sent[0]?.url).toContain(
      "/v1/admin/skill-store/listings/lst-1/takedown",
    );
    expect(sent[0]?.init.method).toBe("POST");
  });

  it("GETs listing body from /v1/admin/skill-store/listings/{id}", async () => {
    const { getSkillStoreListing } = await import("../adminSkillStore");
    const sent: { url: string; init?: RequestInit }[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string, init?: RequestInit) => {
        sent.push({ url, init });
        return Promise.resolve(
          new Response(
            JSON.stringify({
              author: "作者甲",
              author_user_id: "u-author",
              content: "怎么审合同",
              description: "审合同时用",
              id: "lst-1",
              name: "合同审查",
              status: "published",
              updated_at: "2026-09-02T00:00:00Z",
              version_n: 1,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
        );
      }),
    );

    const detail = await getSkillStoreListing("lst-1");
    expect(sent[0]?.url).toContain("/v1/admin/skill-store/listings/lst-1");
    expect(detail.content).toBe("怎么审合同");
  });
});
