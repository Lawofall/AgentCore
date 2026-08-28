/**
 * W3 会话授权根（模型侧 `external/<别名>/`）的桌面侧门。
 *
 * 判据都在这里收口，`dispatch` / `write` 共用同一份：
 * - **模式白名单**：readonly / organize / attach_rw 各自允许哪些 op —— 与服务端
 *   `agentcore/workspace/external_mounts.py` **同结构**（两端都是白名单：新增
 *   `WorkspaceOpName` 默认拒绝，不会在某一端静默放行）。对齐由
 *   `apps/server/tests/test_external_op_parity.py` 穷尽断言。
 * - **可逆性**：区外目录一律软删（见 `write.ts` 的 `opDelete`）。
 * - **分根 copy**：工作区 → organize / attach_rw 时源/目标各走同一套 pathGuard（见
 *   `splitRootCopyError`）；不是放宽守卫。反向 copy 与跨根 move 仍拒。
 *
 * **归属不在这里**：授权根登记时就绑到了 (conversation, device)，路由据此选机
 * （服务端 `fulfill/declare.py`），所以「帧到了本机」本身已经含着「这个会话有权用
 * 这个根」。本机再算一遍只会得到同一个答案——除非算错：那道闸省掉 `conversationId`
 * 就直接放行，而本机 UI 路径正是这么调的，于是它拦不住任何真想绕的人，只会在服务端
 * 与本机对同一个根的归属看法漂移时误伤真实的帧。
 */
import type { WorkspaceOpName, WorkspaceOpResult } from "@shared/ipc-contract";
import type { StoredRoot } from "../roots";
import { opErr } from "./result";

/** Session-root access mode (W3 readonly / organize / attach_rw). Permanent roots have neither. */
export type SessionRootMode = "readonly" | "organize" | "attach_rw";

/** readonly 允许的 op（↔ 服务端 `ORGANIZE_ALLOWED_OPS - ORGANIZE_MUTATION_OPS`）。 */
export const READONLY_ALLOWED_OPS = new Set<WorkspaceOpName>([
  "read",
  "read_bytes",
  "read_head",
  "read_lines",
  "list",
  "exists",
  "list_tree",
  "index_files",
  "grep",
  "diagnostics",
  "probe_exec",
  "process_read",
  "process_list",
  "process_stop",
  "git_repo_status",
]);

/** organize 在只读面之上额外允许的变更 op（↔ 服务端 `ORGANIZE_MUTATION_OPS`）。 */
export const ORGANIZE_MUTATION_OPS = new Set<WorkspaceOpName>([
  "move",
  "copy",
  "mkdir",
  "delete",
]);

/** 显式拒绝面（↔ 服务端 `ORGANIZE_DENIED_OPS`）：写 / 执行 / 打包 / SCM 变更。 */
export const ORGANIZE_DENIED_OPS = new Set<WorkspaceOpName>([
  "write",
  "append",
  "write_bytes",
  "replace",
  "execute",
  "process_start",
  "archive",
  "ensure_turn_baseline",
  "git_scm",
  "git_run",
]);

/** organize 允许面 = 只读面 ∪ 变更面。 */
export const ORGANIZE_ALLOWED_OPS = new Set<WorkspaceOpName>([
  ...READONLY_ALLOWED_OPS,
  ...ORGANIZE_MUTATION_OPS,
]);

const READONLY_MSG =
  "会话授权目录为只读授权，不能改动；如需在此目录交付或整理，请让用户升级为整理授权（交付：先写工作区，再 `file_copy` 到此目录）";
const ORGANIZE_DENY_MSG =
  "整理授权不允许此操作（仅 list/read/grep/stat + move/copy/mkdir + 回收站删除）";
const PERMANENT_EXTERNAL_MSG =
  "区外目录禁止永久删除；请使用可逆删除（进回收站）";
const CROSS_COPY_MSG = "不能跨会话授权目录与工作区复制文件";
const CROSS_MOUNT_COPY_MSG = "不能跨会话授权目录复制文件";
const CROSS_MOVE_MSG = "不能跨会话授权目录与工作区移动文件";
const CROSS_MOUNT_MOVE_MSG = "不能跨会话授权目录移动文件";

/** Resolve session-root mode (missing mode on sessionOnly → readonly). */
export function resolveSessionMode(root: StoredRoot): SessionRootMode | null {
  if (
    root.mode === "organize" ||
    root.mode === "readonly" ||
    root.mode === "attach_rw"
  )
    return root.mode;
  if (root.sessionOnly) return "readonly";
  return null;
}

/** True for W3 会话授权根（区外目录）；产品自己的工作区根为 false。 */
export function isSessionGrantRoot(root: StoredRoot): boolean {
  return resolveSessionMode(root) !== null;
}

/**
 * Mode + op whitelist for session external roots.
 * Returns an error envelope when denied; ``null`` when allowed (or not a session root).
 */
export function sessionRootAccessError(
  root: StoredRoot,
  op: WorkspaceOpName,
  args: Record<string, unknown>,
): WorkspaceOpResult | null {
  const mode = resolveSessionMode(root);
  if (mode === null) return null;

  if (mode === "readonly") {
    return READONLY_ALLOWED_OPS.has(op)
      ? null
      : opErr("OutsideWorkspace", READONLY_MSG);
  }

  if (op === "delete" && Boolean(args.permanent)) {
    return opErr("OutsideWorkspace", PERMANENT_EXTERNAL_MSG);
  }
  if (mode === "attach_rw") {
    const known =
      READONLY_ALLOWED_OPS.has(op) ||
      ORGANIZE_MUTATION_OPS.has(op) ||
      ORGANIZE_DENIED_OPS.has(op);
    return known ? null : opErr("OutsideWorkspace", ORGANIZE_DENY_MSG);
  }
  return ORGANIZE_ALLOWED_OPS.has(op)
    ? null
    : opErr("OutsideWorkspace", ORGANIZE_DENY_MSG);
}

/** Model-facing alias for a session grant; ``null`` = primary workspace. */
export function sessionRootAlias(root: StoredRoot): string | null {
  if (!isSessionGrantRoot(root)) return null;
  const alias = root.alias?.trim();
  return alias || root.id;
}

/**
 * Deny message for copy across two roots, or ``null`` if the direction is allowed.
 *
 * Same alias (including both ``null`` = primary workspace) is in-root copy.
 * Workspace → external is allowed here; the caller still gates dest mode so
 * readonly mounts stay denied. Reverse and cross-mount stay denied.
 */
export function crossRootCopyError(
  srcRoot: StoredRoot,
  dstRoot: StoredRoot,
): string | null {
  const srcAlias = sessionRootAlias(srcRoot);
  const dstAlias = sessionRootAlias(dstRoot);
  if (srcAlias === dstAlias) return null;
  if (srcAlias === null && dstAlias !== null) return null;
  if (srcAlias !== null && dstAlias !== null) return CROSS_MOUNT_COPY_MSG;
  return CROSS_COPY_MSG;
}

/** Deny message for move across roots. Cross-root move is never allowed. */
export function crossRootMoveError(
  srcRoot: StoredRoot,
  dstRoot: StoredRoot,
): string | null {
  const srcAlias = sessionRootAlias(srcRoot);
  const dstAlias = sessionRootAlias(dstRoot);
  if (srcAlias === dstAlias) return null;
  if (srcAlias !== null && dstAlias !== null) return CROSS_MOUNT_MOVE_MSG;
  return CROSS_MOVE_MSG;
}

/**
 * Split-root copy gate: same-root, or workspace → organize / attach_rw.
 * Roots that differ but share a null alias (two workspace ids) still deny —
 * matching server ``src_root != dst_root`` after ``cross_root_copy_error``
 * returns None. Dest mode still gates readonly (copy op denied there first).
 */
export function splitRootCopyError(
  srcRoot: StoredRoot,
  dstRoot: StoredRoot,
): string | null {
  if (srcRoot.id === dstRoot.id) return null;
  const err = crossRootCopyError(srcRoot, dstRoot);
  if (err) return err;
  const dstMode = resolveSessionMode(dstRoot);
  if (
    !isSessionGrantRoot(srcRoot) &&
    (dstMode === "organize" || dstMode === "attach_rw")
  ) {
    return null;
  }
  return CROSS_COPY_MSG;
}

/** Split-root move gate: only same-root. Any other pair is denied. */
export function splitRootMoveError(
  srcRoot: StoredRoot,
  dstRoot: StoredRoot,
): string | null {
  if (srcRoot.id === dstRoot.id) return null;
  return crossRootMoveError(srcRoot, dstRoot) ?? CROSS_MOVE_MSG;
}
