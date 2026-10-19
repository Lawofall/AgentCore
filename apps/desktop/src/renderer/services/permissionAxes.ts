import { hasLocalEngine } from "@/lib/capabilities";
import { uiGet, uiRemove, uiSet } from "@/lib/uiStorage";
import { api } from "@/services/api";
import type { SidecarPermissionAxes } from "@shared/sidecar-contract";

/** Conversation boundary. One control; irreversible actions stay outside it. */
export type WorkspaceBoundaryId = "read" | "folder" | "computer" | "full";

export type PermissionAxes = SidecarPermissionAxes;

export const DEFAULT_PERMISSION_AXES: PermissionAxes = { boundary: "computer" };

export const BOUNDARY_ORDER: WorkspaceBoundaryId[] = [
  "read",
  "folder",
  "computer",
  "full",
];

export const BOUNDARY_LABELS: Record<
  WorkspaceBoundaryId,
  { short: string; description: string }
> = {
  read: {
    short: "只看",
    description: "只能看和搜索。不能改文件、跑命令、用浏览器。",
  },
  folder: {
    short: "本文件夹",
    description:
      "这个文件夹里可以改文件、跑命令、用浏览器。装软件、推远程仍会问。",
  },
  computer: {
    short: "这台电脑",
    description: "这台电脑上的文件和本机操作。装软件、推远程仍会问。",
  },
  full: {
    short: "完全权限",
    description: "不再询问。格式化磁盘、私钥、密码框仍拦住。",
  },
};

const BOUNDARY_IDS = new Set<string>(BOUNDARY_ORDER);

export function axesEqual(a: PermissionAxes, b: PermissionAxes): boolean {
  return a.boundary === b.boundary;
}

export function normalizeAxes(raw: unknown): PermissionAxes {
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    const boundary = (raw as { boundary?: unknown }).boundary;
    if (typeof boundary === "string" && BOUNDARY_IDS.has(boundary)) {
      return { boundary: boundary as WorkspaceBoundaryId };
    }
  }
  return { ...DEFAULT_PERMISSION_AXES };
}

export function boundaryShortLabel(boundary: WorkspaceBoundaryId): string {
  return BOUNDARY_LABELS[boundary].short;
}

/**
 * Human label for audit payloads. Only the current boundary ids.
 * Older recipe / axis snapshots have no label.
 */
export function permissionAxesShortLabel(raw: unknown): string | null {
  if (raw == null) return null;
  if (typeof raw === "string") {
    const trimmed = raw.trim();
    if (BOUNDARY_IDS.has(trimmed)) {
      return boundaryShortLabel(trimmed as WorkspaceBoundaryId);
    }
    if (trimmed.startsWith("{")) {
      try {
        const parsed: unknown = JSON.parse(trimmed);
        if (
          parsed &&
          typeof parsed === "object" &&
          !Array.isArray(parsed) &&
          typeof (parsed as { boundary?: unknown }).boundary === "string" &&
          BOUNDARY_IDS.has((parsed as { boundary: string }).boundary)
        ) {
          return boundaryShortLabel(
            (parsed as { boundary: WorkspaceBoundaryId }).boundary,
          );
        }
      } catch {
        return null;
      }
      return null;
    }
    return null;
  }
  if (typeof raw === "object" && !Array.isArray(raw)) {
    const boundary = (raw as { boundary?: unknown }).boundary;
    if (typeof boundary === "string" && BOUNDARY_IDS.has(boundary)) {
      return boundaryShortLabel(boundary as WorkspaceBoundaryId);
    }
  }
  return null;
}

const COMPUTER_CONFIRM =
  "这台电脑上的文件和本机操作都会放行。装软件、推远程仍会问。格式化磁盘、私钥、密码框仍拦住。确定继续？";

const FULL_CONFIRM =
  "完全权限下不再询问，包括推主干和强推。格式化磁盘、私钥、密码框仍拦住。确定继续？";

export function needsComputerConfirm(
  current: PermissionAxes,
  next: PermissionAxes,
): boolean {
  return (
    next.boundary === "computer" &&
    current.boundary !== "computer" &&
    current.boundary !== "full"
  );
}

export function needsFullConfirm(
  current: PermissionAxes,
  next: PermissionAxes,
): boolean {
  return next.boundary === "full" && current.boundary !== "full";
}

export function confirmComputerIfNeeded(
  current: PermissionAxes,
  next: PermissionAxes,
): boolean {
  if (needsFullConfirm(current, next)) return window.confirm(FULL_CONFIRM);
  if (!needsComputerConfirm(current, next)) return true;
  return window.confirm(COMPUTER_CONFIRM);
}

/** Last boundary the person picked in the composer. Seeds the next new chat. */
const LAST_USED_BOUNDARY_LEAF = "chat:boundary:last";

let cachedDefaultAxes: PermissionAxes | null = null;
let composerDraftAxes: PermissionAxes | null = null;

export function setComposerDraftAxes(axes: PermissionAxes | null): void {
  composerDraftAxes = axes ? normalizeAxes(axes) : null;
}

export function peekComposerDraftAxes(): PermissionAxes | null {
  return composerDraftAxes;
}

function clampBoundaryForRuntime(
  boundary: WorkspaceBoundaryId,
): WorkspaceBoundaryId {
  if (boundary === "computer" && !hasLocalEngine()) return "folder";
  return boundary;
}

function clampAxes(axes: PermissionAxes): PermissionAxes {
  const boundary = clampBoundaryForRuntime(axes.boundary);
  return boundary === axes.boundary ? axes : { boundary };
}

function readLastUsedBoundary(): WorkspaceBoundaryId | null {
  const raw = uiGet(LAST_USED_BOUNDARY_LEAF);
  if (typeof raw === "string" && BOUNDARY_IDS.has(raw)) {
    return raw as WorkspaceBoundaryId;
  }
  return null;
}

/**
 * 人选的那档记在本机 last-used，并静默写账号（省略 body 的创建接口用）。
 * 从没选过 = 出厂这台电脑；网页没有本机引擎时收成本文件夹。「完全权限」可开。
 */
export function factoryPermissionAxes(): PermissionAxes {
  return clampAxes({ ...DEFAULT_PERMISSION_AXES });
}

export function rememberBoundarySelection(boundary: WorkspaceBoundaryId): void {
  const stored = BOUNDARY_IDS.has(boundary)
    ? boundary
    : DEFAULT_PERMISSION_AXES.boundary;
  uiSet(LAST_USED_BOUNDARY_LEAF, stored);
  cachedDefaultAxes = { boundary: clampBoundaryForRuntime(stored) };
  void api
    .put<{ policy: WorkspaceBoundaryId }>("/v1/users/me/autonomy", {
      policy: stored,
    })
    .catch(() => {
      /* this device already has last-used */
    });
}

export async function resolveDefaultPermissionAxes(): Promise<PermissionAxes> {
  if (composerDraftAxes) return clampAxes(composerDraftAxes);
  const local = readLastUsedBoundary();
  if (local) {
    cachedDefaultAxes = { boundary: clampBoundaryForRuntime(local) };
    return cachedDefaultAxes;
  }
  if (cachedDefaultAxes) return clampAxes(cachedDefaultAxes);
  cachedDefaultAxes = factoryPermissionAxes();
  return cachedDefaultAxes;
}

export function clearDefaultPermissionAxesCache(): void {
  cachedDefaultAxes = null;
  composerDraftAxes = null;
  uiRemove(LAST_USED_BOUNDARY_LEAF);
}

/** Persist a mid-session boundary switch. Returns the saved axes. */
export async function setConversationPermissionAxes(
  conversationId: string,
  permissionAxes: PermissionAxes,
): Promise<PermissionAxes> {
  const res = await api.put<{
    permission_axes?: PermissionAxes;
  }>(`/v1/conversations/${conversationId}/permission-axes`, {
    permission_axes: normalizeAxes(permissionAxes),
  });
  return normalizeAxes(res.permission_axes ?? permissionAxes);
}

/**
 * Resolve axes for a conversation (React Query cache first, else GET).
 * Sidecar turns send this every startTurn / resume.
 */
export async function resolveConversationPermissionAxes(
  conversationId: string,
): Promise<PermissionAxes | undefined> {
  try {
    const { getConversations } = await import("@/hooks/useConversations");
    const conv = getConversations().find((c) => c.id === conversationId);
    if (conv?.permissionAxes) return normalizeAxes(conv.permissionAxes);
  } catch {
    // query cache may be unavailable in tests
  }
  try {
    const res = await api.get<{ permission_axes?: PermissionAxes }>(
      `/v1/conversations/${conversationId}`,
    );
    if (res.permission_axes) return normalizeAxes(res.permission_axes);
  } catch {
    // network / 404 — last resort below
  }
  return resolveDefaultPermissionAxes();
}

/** Conversation's current boundary from the list cache. Miss → undefined (do not invent). */
export async function peekConversationBoundary(
  conversationId: string,
): Promise<WorkspaceBoundaryId | undefined> {
  try {
    const { getConversations } = await import("@/hooks/useConversations");
    const conv = getConversations().find((c) => c.id === conversationId);
    if (conv?.permissionAxes)
      return normalizeAxes(conv.permissionAxes).boundary;
  } catch {
    /* tests / cache unavailable */
  }
  return undefined;
}
