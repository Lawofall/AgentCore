import { BASE_URL } from "@/services/api";
import { createDocShare, listDocShares, revokeDocShare } from "@/services/docs";
import type { Share } from "@/services/sharing";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const share = (over: Partial<Share> = {}): Share => ({
  id: "s1",
  url: "/shared/s1",
  title: "T",
  created_at: "2026-01-01T00:00:00Z",
  ...over,
});

const okJson = (body: unknown) =>
  new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });

let fetchMock: ReturnType<typeof vi.fn>;
beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

describe("doc share CRUD", () => {
  it("createDocShare POSTs to the doc shares endpoint", async () => {
    const made = share({ id: "new" });
    fetchMock.mockResolvedValue(okJson(made));

    const res = await createDocShare("d1");

    expect(res).toEqual(made);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_URL}/v1/docs/d1/shares`);
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(init.credentials).toBe("include");
  });

  it("listDocShares unwraps the paginated data array", async () => {
    const data = [share({ id: "a" })];
    fetchMock.mockResolvedValue(okJson({ data, total: 1 }));

    const res = await listDocShares("d1");

    expect(res).toEqual(data);
    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_URL}/v1/docs/d1/shares`);
  });

  it("revokeDocShare DELETEs the specific share by id", async () => {
    fetchMock.mockResolvedValue(okJson({ status: "ok" }));

    await revokeDocShare("d1", "s9");

    expect(fetchMock.mock.calls[0][0]).toBe(`${BASE_URL}/v1/docs/d1/shares/s9`);
    expect((fetchMock.mock.calls[0][1] as RequestInit).method).toBe("DELETE");
  });
});
