// @vitest-environment jsdom
import type { ExternalMountRequiredPayload } from "@/types/events";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const resolveInteraction = vi.fn().mockResolvedValue(undefined);
const pickAndGrantReadonlyFolder = vi.fn();
const pickAndGrantSessionFolder = vi.fn();

vi.mock("@/services/interaction", () => ({
  resolveInteraction: (...args: unknown[]) => resolveInteraction(...args),
}));

vi.mock("@/lib/grantReadonlyFolder", () => ({
  pickAndGrantReadonlyFolder: (...args: unknown[]) =>
    pickAndGrantReadonlyFolder(...args),
}));

vi.mock("@/lib/grantOrganizeFolder", () => ({
  pickAndGrantSessionFolder: (...args: unknown[]) =>
    pickAndGrantSessionFolder(...args),
}));

import { resetClientToolFulfillmentForTests } from "../clientToolFulfill";
import { performExternalMount } from "../externalMountOps";

type MountPayload = ExternalMountRequiredPayload;

function payload(over: Partial<MountPayload> = {}): MountPayload {
  return {
    request_id: "req-1",
    conversation_id: "conv-1",
    well_known: "desktop",
    target_name: "咨询",
    ...over,
  };
}

describe("performExternalMount", () => {
  beforeEach(() => {
    resetClientToolFulfillmentForTests();
    resolveInteraction.mockClear();
    pickAndGrantReadonlyFolder.mockReset();
    pickAndGrantSessionFolder.mockReset();
    vi.spyOn(window, "confirm").mockReturnValue(true);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("grants via IPC+POST and posts client_tool result (no abs)", async () => {
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "咨询", alias: "咨询" },
      alias: "咨询",
      namespace: "external/咨询",
      displayLabel: "咨询",
    });

    await performExternalMount(payload(), "conv-1", "cloud");

    expect(pickAndGrantReadonlyFolder).toHaveBeenCalledWith("conv-1", {
      wellKnown: "desktop",
      targetName: "咨询",
    });
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        kind: "client_tool",
        ok: true,
        value: {
          root_id: "root-1",
          alias: "咨询",
          label: "咨询",
          display_label: "咨询",
          namespace: "external/咨询",
        },
      }),
      "cloud",
    );
    const posted = resolveInteraction.mock.calls[0][2] as {
      value: Record<string, unknown>;
    };
    expect(posted.value).not.toHaveProperty("abs");
    expect(posted.value).not.toHaveProperty("absPath");
    expect(posted.value).not.toHaveProperty("path");
  });

  it("maps not_found to tool failure (detail + reason)", async () => {
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "not_found",
      message: "找不到该目录",
    });

    await performExternalMount(
      payload({
        path: "C:/missing",
        well_known: undefined,
        target_name: undefined,
      }),
      "conv-1",
      "cloud",
    );

    expect(pickAndGrantReadonlyFolder).toHaveBeenCalledWith("conv-1", {
      path: "C:/missing",
    });
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        kind: "client_tool",
        ok: false,
        error: {
          kind: "ExternalMountError",
          detail: "找不到该目录",
          reason: "not_found",
        },
      }),
      "cloud",
    );
  });

  it("maps not_directory / ambiguous with structured reason", async () => {
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "ambiguous",
      message: "匹配到多个目录，请说得更具体",
    });

    await performExternalMount(payload(), "conv-1", "cloud");

    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        ok: false,
        error: {
          kind: "ExternalMountError",
          detail: "匹配到多个目录，请说得更具体",
          reason: "ambiguous",
        },
      }),
      "cloud",
    );
  });

  it("fails cleanly when desktop channel unavailable", async () => {
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: false,
      reason: "unavailable",
    });

    await performExternalMount(payload(), "conv-1", "cloud");

    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        ok: false,
        error: {
          kind: "ExternalMountError",
          detail: "非桌面环境，无法挂载本机目录",
          reason: "unavailable",
        },
      }),
      "cloud",
    );
  });

  it("does not re-grant on a second perform with the same request_id", async () => {
    pickAndGrantReadonlyFolder.mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "咨询", alias: "咨询" },
      alias: "咨询",
      namespace: "external/咨询",
    });

    await performExternalMount(payload(), "conv-1", "cloud");
    await performExternalMount(payload(), "conv-1", "cloud");

    expect(pickAndGrantReadonlyFolder).toHaveBeenCalledTimes(1);
    expect(resolveInteraction).toHaveBeenCalledTimes(1);
  });

  it("organize + root_id upgrades without window.confirm", async () => {
    pickAndGrantSessionFolder.mockResolvedValue({
      ok: true,
      root: { id: "root-1", name: "咨询", alias: "desk", mode: "organize" },
      alias: "desk",
      namespace: "external/desk",
      displayLabel: "咨询",
    });

    await performExternalMount(
      payload({
        well_known: undefined,
        target_name: undefined,
        mode: "organize",
        root_id: "root-1",
      }),
      "conv-1",
      "cloud",
    );

    expect(window.confirm).not.toHaveBeenCalled();
    expect(pickAndGrantReadonlyFolder).not.toHaveBeenCalled();
    expect(pickAndGrantSessionFolder).toHaveBeenCalledWith(
      "conv-1",
      "organize",
      { rootId: "root-1" },
    );
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        ok: true,
        value: expect.objectContaining({ root_id: "root-1" }),
      }),
      "cloud",
    );
  });

  it("maps cancelled from main-process dialog", async () => {
    pickAndGrantSessionFolder.mockResolvedValue({
      ok: false,
      reason: "cancelled",
      message: "用户拒绝授权",
    });

    await performExternalMount(
      payload({
        mode: "organize",
        root_id: "root-1",
        well_known: undefined,
        target_name: undefined,
      }),
      "conv-1",
      "cloud",
    );

    expect(window.confirm).not.toHaveBeenCalled();
    expect(resolveInteraction).toHaveBeenCalledWith(
      "conv-1",
      "req-1",
      expect.objectContaining({
        ok: false,
        error: {
          kind: "ExternalMountError",
          detail: "用户拒绝授权",
          reason: "cancelled",
        },
      }),
      "cloud",
    );
  });
});
