/**
 * host(action=shell) cwd — runtime-injected, never a model-filled abs path.
 *
 * Authorized world = permanent roots ∪ this conversation's session grants
 * (readonly / organize / attach_rw). No roots (裸聊 Host / unit tests) → homedir.
 * Industry default (Cursor / Claude Code / VS Code): project workspace, not $HOME.
 * A raw rootId is a default-cwd hint, not a way to enlarge the authorized set.
 */
import { isAbsolute, relative, resolve } from "node:path";
import { realpath } from "node:fs/promises";
import os from "node:os";
import {
  ensureReady,
  getAllRoots,
  getRoot,
  listSessionRoots,
} from "../fs/roots";

export const HOST_SHELL_CWD_DENIED =
  "host(action=shell) 的工作目录必须落在已授权目录内（工作区根或本对话附加文件夹）。";

export type HostShellCwdHint = {
  cwd?: string;
  conversationId?: string;
  rootId?: string;
};

export function isAbsInsideRoot(abs: string, rootAbs: string): boolean {
  const rel = relative(resolve(rootAbs), resolve(abs));
  if (rel === "") return true;
  if (isAbsolute(rel)) return false;
  return !rel.split(/[/\\]/).includes("..");
}

async function realAbs(p: string): Promise<string> {
  const resolved = resolve(p);
  try {
    return await realpath(resolved);
  } catch {
    return resolved;
  }
}

export async function resolveHostShellCwd(
  hint: HostShellCwdHint = {},
): Promise<{ ok: true; cwd: string } | { ok: false; error: string }> {
  await ensureReady();
  const requested = (hint.cwd || "").trim();
  const rootId = (hint.rootId || "").trim();
  const conversationId = (hint.conversationId || "").trim();

  const authorized = [
    ...getAllRoots(),
    ...(conversationId ? listSessionRoots(conversationId) : []),
  ];

  let want = requested;
  if (!want && rootId) {
    const bound = getRoot(rootId);
    if (bound && authorized.some((r) => r.id === bound.id)) {
      want = bound.absPath?.trim() || "";
    }
  }
  if (!want) {
    if (authorized.length === 0) {
      return { ok: true, cwd: os.homedir() };
    }
    const permanent = getAllRoots()[0];
    want = (permanent ?? authorized[0]).absPath;
  }

  const resolved = await realAbs(want);
  for (const r of authorized) {
    if (!r.absPath) continue;
    const rootResolved = await realAbs(r.absPath);
    if (isAbsInsideRoot(resolved, rootResolved)) {
      return { ok: true, cwd: resolved };
    }
  }
  return { ok: false, error: HOST_SHELL_CWD_DENIED };
}
