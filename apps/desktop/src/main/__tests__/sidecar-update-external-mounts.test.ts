/**
 * SidecarManager.pushLiveExternalMounts — mid-turn abs snapshot, no spawn.
 * @vitest-environment node
 */
import { afterAll, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => {
  const base = process.env.TEMP || process.env.TMPDIR || "/tmp";
  const sessionRoots: Array<{
    id: string;
    name: string;
    absPath: string;
    alias?: string;
    sessionOnly: boolean;
    conversationId: string;
    mode: "readonly" | "organize" | "attach_rw";
  }> = [];
  return {
    dir: `${base}/sidecar-ext-mounts-${Math.random().toString(36).slice(2)}`,
    sessionRoots,
  };
});

vi.mock("electron", () => ({
  app: { on: vi.fn(), getAppPath: () => "", getPath: () => h.dir },
  ipcMain: { handle: vi.fn() },
  BrowserWindow: { getAllWindows: () => [] },
}));

vi.mock("../log-service", () => ({
  logDesktop: vi.fn(),
}));

vi.mock("../outbox/projection", () => ({
  occupyLocalTurnBegin: vi.fn(async () => true),
  abortLocalTurnPlaceholder: vi.fn(async () => undefined),
}));

vi.mock("../fs/roots", () => ({
  listSessionRoots: () => h.sessionRoots,
}));

import { rmSync } from "node:fs";
import { SidecarManager } from "../sidecar/manager";
import type { Transport } from "../sidecar/transport";

function rpcTransport() {
  let lineCb: ((line: string) => void) | null = null;
  const sent: Array<{ method: string; params: Record<string, unknown> }> = [];
  const transport: Transport = {
    send: (line) => {
      const msg = JSON.parse(line) as {
        id?: number;
        method?: string;
        params?: Record<string, unknown>;
      };
      if (msg.method) {
        sent.push({ method: msg.method, params: msg.params ?? {} });
      }
      if (typeof msg.id === "number") {
        queueMicrotask(() => {
          lineCb?.(
            JSON.stringify({
              jsonrpc: "2.0",
              id: msg.id,
              result: { ok: true, attached: true },
            }),
          );
        });
      }
    },
    onLine: (cb) => {
      lineCb = cb;
    },
    onClose: () => {},
    close: vi.fn(),
  };
  return { transport, sent };
}

function injectLiveTurn(manager: SidecarManager): void {
  (
    manager as unknown as {
      turns: Map<
        string,
        { conversationId: string; rootId: string; subpath: string }
      >;
    }
  ).turns.set("t1", {
    conversationId: "c1",
    rootId: "r1",
    subpath: "",
  });
}

describe("SidecarManager.pushLiveExternalMounts", () => {
  afterAll(() => rmSync(h.dir, { recursive: true, force: true }));

  it("无 sidecar 进程时静默返回、不 spawn", async () => {
    const manager = new SidecarManager(() => {
      throw new Error("must not spawn");
    });
    await expect(manager.pushLiveExternalMounts("c1")).resolves.toBeUndefined();
  });

  it("有进程但无活回合时不发 updateExternalMounts", async () => {
    const t = rpcTransport();
    const manager = new SidecarManager(() => t.transport);
    await manager.probe("r1", "", "/tmp/ws");
    await manager.pushLiveExternalMounts("c1");
    expect(t.sent.map((s) => s.method)).toEqual(["initialize"]);
  });

  it("活回合把当前会话快照热推到 sidecar", async () => {
    h.sessionRoots.splice(0, h.sessionRoots.length, {
      id: "r-ext",
      name: "桌面",
      absPath: "C:\\Users\\me\\Desktop",
      alias: "desk",
      sessionOnly: true,
      conversationId: "c1",
      mode: "organize",
    });
    const t = rpcTransport();
    const manager = new SidecarManager(() => t.transport);
    await manager.probe("r1", "", "/tmp/ws");
    injectLiveTurn(manager);
    await manager.pushLiveExternalMounts("c1");
    const push = t.sent.find((s) => s.method === "updateExternalMounts");
    expect(push?.params).toEqual({
      conversationId: "c1",
      externalMounts: [
        {
          alias: "desk",
          rootId: "r-ext",
          label: "桌面",
          absPath: "C:\\Users\\me\\Desktop",
          mode: "organize",
        },
      ],
    });
  });
});
