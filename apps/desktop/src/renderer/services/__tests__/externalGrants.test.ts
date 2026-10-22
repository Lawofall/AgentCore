import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/queryClient", () => ({
  queryClient: { invalidateQueries: vi.fn() },
}));

vi.mock("@/services/api", () => ({
  api: { get: vi.fn() },
}));

import { queryClient } from "@/lib/queryClient";
import { api } from "@/services/api";
import {
  externalGrantModeLabel,
  invalidateExternalGrants,
  listExternalGrants,
} from "../externalGrants";

describe("externalGrants", () => {
  beforeEach(() => {
    vi.mocked(api.get).mockReset();
    vi.mocked(queryClient.invalidateQueries).mockReset();
  });

  it("maps mode labels", () => {
    expect(externalGrantModeLabel("readonly")).toBe("只读");
    expect(externalGrantModeLabel("organize")).toBe("整理");
    expect(externalGrantModeLabel("attach_rw")).toBe("可改");
  });

  it("lists grants from GET external-grants", async () => {
    vi.mocked(api.get).mockResolvedValueOnce({
      data: [
        {
          root_id: "r1",
          alias: "咨询",
          label: "咨询",
          namespace: "external/咨询",
          mode: "readonly",
        },
      ],
    });
    const rows = await listExternalGrants("conv-1");
    expect(api.get).toHaveBeenCalledWith(
      "/v1/conversations/conv-1/workspace/external-grants",
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.namespace).toBe("external/咨询");
  });

  it("invalidates the conversation list query key", () => {
    invalidateExternalGrants("conv-9");
    expect(queryClient.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ["external-grants", "list", "conv-9"],
    });
  });
});
