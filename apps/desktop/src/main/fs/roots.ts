import { promises as fs } from "node:fs";
import { join } from "node:path";
import { app } from "electron";

export interface StoredRoot {
  id: string;
  name: string;
  absPath: string;
  /** W3: conversation-scoped grant — persisted to fs-session-grants.json, not fs-roots. */
  sessionOnly?: boolean;
  conversationId?: string;
  /**
   * Session access mode.
   * ``readonly`` = W3 read-only; ``organize`` = move/copy/mkdir/trash-delete;
   * ``attach_rw`` = 本机传统附加可写（可覆盖）。
   */
  mode?: "readonly" | "organize" | "attach_rw";
  /** Model-facing alias under ``external/<alias>/``. */
  alias?: string;
}

/** Raw disk row — may still carry a legacy ``readonly`` boolean (migrated on load). */
type SessionGrantDiskRow = {
  id?: string;
  name?: string;
  absPath?: string;
  sessionOnly?: boolean;
  conversationId?: string;
  mode?: unknown;
  readonly?: unknown;
  alias?: string;
};

/** On-disk shape: conversationId → session grant rows (paths stay on desktop). */
type SessionGrantsFile = Record<string, SessionGrantDiskRow[]>;

let roots = new Map<string, StoredRoot>();
let rootsReady: Promise<void> | null = null;

function storeFilePath(): string {
  return join(app.getPath("userData"), "fs-roots.json");
}

function sessionGrantsFilePath(): string {
  return join(app.getPath("userData"), "fs-session-grants.json");
}

/** One-shot coerce: explicit mode wins; legacy boolean-only rows → mode. */
function coerceSessionMode(
  mode: unknown,
  legacyReadonly: unknown,
): "readonly" | "organize" | "attach_rw" {
  if (mode === "organize" || mode === "readonly" || mode === "attach_rw")
    return mode;
  if (legacyReadonly === false) return "organize";
  return "readonly";
}

function sessionRootPayload(r: StoredRoot): StoredRoot {
  return {
    id: r.id,
    name: r.name,
    absPath: r.absPath,
    sessionOnly: true,
    conversationId: r.conversationId,
    mode: coerceSessionMode(r.mode, undefined),
    alias: r.alias,
  };
}

function fromDiskRow(
  row: SessionGrantDiskRow,
  conversationId: string,
): StoredRoot | null {
  if (!row?.id || !row?.absPath) return null;
  return {
    id: row.id,
    name: typeof row.name === "string" ? row.name : row.id,
    absPath: row.absPath,
    sessionOnly: true,
    conversationId: row.conversationId ?? conversationId,
    mode: coerceSessionMode(row.mode, row.readonly),
    alias: typeof row.alias === "string" ? row.alias : undefined,
  };
}

async function loadSessionGrants(): Promise<void> {
  try {
    const raw = await fs.readFile(sessionGrantsFilePath(), "utf-8");
    const data = JSON.parse(raw) as SessionGrantsFile;
    for (const [conversationId, arr] of Object.entries(data)) {
      if (!Array.isArray(arr)) continue;
      for (const row of arr) {
        const normalized = fromDiskRow(row, conversationId);
        if (normalized) roots.set(normalized.id, normalized);
      }
    }
  } catch {
    // Missing / corrupt → empty session grants (permanent roots already loaded).
  }
}

async function saveSessionGrants(): Promise<void> {
  const byConv: SessionGrantsFile = {};
  for (const r of roots.values()) {
    if (!r.sessionOnly || !r.conversationId) continue;
    let list = byConv[r.conversationId];
    if (!list) {
      list = [];
      byConv[r.conversationId] = list;
    }
    list.push(sessionRootPayload(r));
  }
  try {
    await fs.writeFile(
      sessionGrantsFilePath(),
      JSON.stringify(byConv, null, 2),
    );
  } catch (e) {
    console.error("[fs-service] 持久化会话授权根失败:", e);
  }
}

async function loadRoots(): Promise<void> {
  try {
    const raw = await fs.readFile(storeFilePath(), "utf-8");
    const arr = JSON.parse(raw) as StoredRoot[];
    // Permanent roots only — session grants live in fs-session-grants.json.
    roots = new Map(
      arr
        .filter((r) => !r.sessionOnly)
        .map((r) => [r.id, { id: r.id, name: r.name, absPath: r.absPath }]),
    );
  } catch {
    roots = new Map();
  }
  await loadSessionGrants();
}

async function saveRoots(): Promise<void> {
  const arr = [...roots.values()].filter((r) => !r.sessionOnly);
  try {
    await fs.writeFile(storeFilePath(), JSON.stringify(arr, null, 2));
  } catch (e) {
    console.error("[fs-service] 持久化授权根失败:", e);
  }
}

export async function ensureReady(): Promise<void> {
  if (rootsReady) await rootsReady;
}

export function initRoots(): void {
  rootsReady = loadRoots();
}

export function getRoot(id: string): StoredRoot | undefined {
  return roots.get(id);
}

export function setRoot(root: StoredRoot): void {
  roots.set(root.id, root);
}

export function deleteRoot(id: string): boolean {
  return roots.delete(id);
}

/** Permanent (non-session) roots for settings / project binding. */
export function getAllRoots(): StoredRoot[] {
  return [...roots.values()].filter((r) => !r.sessionOnly);
}

export function findRootByAbsPath(absPath: string): StoredRoot | undefined {
  return [...roots.values()].find((r) => r.absPath === absPath);
}

export function listSessionRoots(conversationId: string): StoredRoot[] {
  return [...roots.values()].filter(
    (r) => r.sessionOnly && r.conversationId === conversationId,
  );
}

export function clearSessionRoots(conversationId: string): string[] {
  const removed: string[] = [];
  for (const [id, r] of roots) {
    if (r.sessionOnly && r.conversationId === conversationId) {
      roots.delete(id);
      removed.push(id);
    }
  }
  return removed;
}

export function revokeSessionRoot(
  conversationId: string,
  rootId: string,
): boolean {
  const r = roots.get(rootId);
  if (!r?.sessionOnly || r.conversationId !== conversationId) return false;
  roots.delete(rootId);
  return true;
}

/**
 * 按 id 取一个已授权根（含绝对路径），供 sidecar 模式把 `rootId` 解析成 `workspaceRoot`。
 *
 * 与 renderer 的 `{rootId, relPath}` 寻址同源（绝对路径只存在于主进程）；本地引擎
 * （sidecar）跑在用户机器上，需要这个绝对路径作为绑定根。未授权 / 已移除返回 null。
 */
export async function getStoredRoot(
  rootId: string,
): Promise<StoredRoot | null> {
  await ensureReady();
  return roots.get(rootId) ?? null;
}

export { saveRoots, saveSessionGrants };

/** Test helpers: inject / read without Electron app paths. */
export const __test = {
  reset(map?: Map<string, StoredRoot>) {
    roots = map ?? new Map();
    rootsReady = Promise.resolve();
  },
  getMap() {
    return roots;
  },
  sessionGrantsFilePath,
  buildSessionFilePayload(): SessionGrantsFile {
    const byConv: SessionGrantsFile = {};
    for (const r of roots.values()) {
      if (!r.sessionOnly || !r.conversationId) continue;
      let list = byConv[r.conversationId];
      if (!list) {
        list = [];
        byConv[r.conversationId] = list;
      }
      list.push(sessionRootPayload(r));
    }
    return byConv;
  },
  applySessionFilePayload(data: SessionGrantsFile) {
    for (const [conversationId, arr] of Object.entries(data)) {
      if (!Array.isArray(arr)) continue;
      for (const row of arr) {
        const normalized = fromDiskRow(row, conversationId);
        if (normalized) roots.set(normalized.id, normalized);
      }
    }
  },
};
