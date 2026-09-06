/**
 * 时序收口：「挂载成功」回执到服务端之前，登记必须已经落。
 *
 * 登记就是声明——`POST …/external-grants` 那一趟把 root 绑到本设备的履约会话上，
 * 所以它返回之前这个 root 在服务端还没有履约方。而服务端拿到挂载回执就会在同一轮里
 * 对该 root 下发操作，no fulfiller 是立即结算失败并熔断该工具，一轮内不重试、不自愈。
 *
 * 走真实的 `grantReadonlyFolder`（只桩掉 IPC / HTTP），因为要钉的正是两次网络动作的
 * **先后**。
 * @vitest-environment jsdom
 */
import type { ExternalMountRequiredPayload } from "@/types/events";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** 调用顺序流水：登记与别名落地都必须排在回执之前。 */
const order: string[] = [];
const resolveInteraction = vi.fn(async (..._args: unknown[]) => {
  order.push("settle");
});
const apiPost = vi.fn();
const grantSessionReadonlyRoot = vi.fn();
const adoptSessionRootAlias = vi.fn(async () => {
  order.push("alias");
  return true;
});

vi.mock("@/lib/capabilities", () => ({
  hasLocalFiles: () => true,
}));

vi.mock("@/lib/revokeExternalGrant", () => ({
  revokeExternalGrant: vi.fn(),
}));

vi.mock("@/services/externalGrants", () => ({
  invalidateExternalGrants: vi.fn(),
}));

vi.mock("@/services/interaction", () => ({
  resolveInteraction: (...args: unknown[]) => resolveInteraction(...args),
}));

vi.mock("@/services/api", () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
  NetworkError: class NetworkError extends Error {},
  api: { post: (...args: unknown[]) => apiPost(...args) },
}));

import { resetClientToolFulfillmentForTests } from "../clientToolFulfill";
import { performExternalMount } from "../externalMountOps";

function payload(
  over: Partial<ExternalMountRequiredPayload> = {},
): ExternalMountRequiredPayload {
  return {
    request_id: "req-1",
    conversation_id: "conv-1",
    well_known: "desktop",
    target_name: "咨询",
    ...over,
  };
}

describe("external_mount 成功回执与授权登记的时序", () => {
  beforeEach(() => {
    resetClientToolFulfillmentForTests();
    order.length = 0;
    resolveInteraction.mockClear();
    apiPost.mockReset();
    grantSessionReadonlyRoot.mockReset();
    adoptSessionRootAlias.mockClear();
    window.fsApi = {
      grantSessionReadonlyRoot,
      adoptSessionRootAlias,
    } as unknown as typeof window.fsApi;
  });

  it("挂载成功回执前，登记已返回、别名已落地", async () => {
    grantSessionReadonlyRoot.mockResolvedValue({
      ok: true,
      root: { id: "sess-ext-1", name: "咨询", mode: "readonly" },
      displayLabel: "桌面 › 咨询",
    });
    apiPost.mockImplementation(async () => {
      order.push("register");
      return { grant: { alias: "咨询", namespace: "external/咨询" } };
    });

    await performExternalMount(payload(), "conv-1", "cloud");

    expect(order).toEqual(["register", "alias", "settle"]);
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        kind: "client_tool",
        ok: true,
        value: expect.objectContaining({ root_id: "sess-ext-1" }),
      }),
      "cloud",
    );
  });

  it("授权没成时不登记，也照常回失败", async () => {
    grantSessionReadonlyRoot.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    await performExternalMount(payload(), "conv-1", "cloud");

    expect(apiPost).not.toHaveBeenCalled();
    expect(order).toEqual(["settle"]);
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({ ok: false }),
      "cloud",
    );
  });
});
