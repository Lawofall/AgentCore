/**
 * 云会话「电脑当家」原件偏好（桌面 UI）。
 *
 * 按 folderId 记录借用的本机根；`promoted` 后不再提示原件未改。
 * 仅 uiStorage，不改会话 folder_id。
 */

import { uiGet, uiSet } from "@/lib/uiStorage";

const STORAGE_KEY = "borrow-original";

export type BorrowOriginalPreference = {
  rootId: string;
  originalName: string;
  promoted: boolean;
};

type Store = Record<string, BorrowOriginalPreference>;

function folderKey(folderId: string): string {
  return folderId.trim();
}

function parseEntry(raw: unknown): BorrowOriginalPreference | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const rootId = (raw as { rootId?: unknown }).rootId;
  const originalName = (raw as { originalName?: unknown }).originalName;
  const promoted = (raw as { promoted?: unknown }).promoted;
  if (typeof rootId !== "string" || !rootId.trim()) return null;
  if (typeof originalName !== "string") return null;
  if (typeof promoted !== "boolean") return null;
  return {
    rootId: rootId.trim(),
    originalName,
    promoted,
  };
}

function readStore(): Store {
  const raw = uiGet<unknown>(STORAGE_KEY);
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return {};
  const out: Store = {};
  for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
    if (!k) continue;
    const entry = parseEntry(v);
    if (entry) out[k] = entry;
  }
  return out;
}

function writeStore(store: Store): void {
  if (Object.keys(store).length === 0) uiSet(STORAGE_KEY, undefined);
  else uiSet(STORAGE_KEY, store);
}

export function get(folderId: string): BorrowOriginalPreference | null {
  const id = folderKey(folderId);
  if (!id) return null;
  return readStore()[id] ?? null;
}

export function set(folderId: string, pref: BorrowOriginalPreference): void {
  const id = folderKey(folderId);
  const entry = parseEntry(pref);
  if (!id || !entry) return;
  const store = readStore();
  store[id] = entry;
  writeStore(store);
}

export function markPromoted(folderId: string): void {
  const current = get(folderId);
  if (!current || current.promoted) return;
  set(folderId, { ...current, promoted: true });
}

export function isBorrowActive(folderId: string): boolean {
  const current = get(folderId);
  return current != null && !current.promoted;
}
