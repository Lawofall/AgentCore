import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { hasLocalEngine } from "@/lib/capabilities";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { bareConversationScratchSubpath } from "@/services/bareScratchPath";
import type { WorkspaceInfo } from "@/services/workspaces";
import {
  localEngineOffMessage,
  workspaceRootAbsentMessage,
  workspaceRootGoneMessage,
} from "@shared/workspaceRootGone";

/**
 * 会话路由判定：一个回合该走本地 sidecar，还是云端 SSE。
 *
 * 双模式工作区 §7.2：本机传统（`mode=local` + 活本机根）新开回合**只走** sidecar；
 * 云协作永不 sidecar。死绑定、授权表没有这个 id、这台客户端没有本地引擎、占位失败：
 * 这一轮不开始，不降级云。
 *
 * 续跑例外：`origin=sidecar` / 已有本机活回合须跟本地事实（{@link resolveLocalBind}
 * / {@link getActiveSidecarTarget}）——本机帧云端没有。
 *
 * sidecar 暂非真离线（LLM 仍经云推理代理）、被委派 worker 仍走审批门。
 */

/**
 * 一次 sidecar 回合的寻址目标：本地容器根 id + 工作区子路径（conversation scratch）。
 *
 * `subpath` 空 = 该根自身；非空 = 该容器根下 per-conversation scratch 子目录。主进程据
 * `rootId + subpath` 把 sidecar 进程绑定到 `容器根/子路径`。
 */
export interface SidecarTarget {
  rootId: string;
  subpath: string;
}

/**
 * 本机绑定：未绑 / 活 / 死 / 这台电脑没有这个 id / 本地引擎不能接。
 *
 * - unbound：没有本机绑定 → 新回合走云
 * - live：授权根在表；空子路径目录在盘上，或非空子路径可 mkdir
 * - stale：授权根在表，空子路径却不是目录 → 不 probe、不 spawn、不走云
 * - absent：会话记着 root id，授权表里没有 → 不走云，请重新选择位置
 * - engine_off：有本机绑定，但这台客户端没有本地引擎 → 不走云
 */
export type LocalBindResolution =
  | { kind: "unbound" }
  | { kind: "live"; rootId: string; subpath: string }
  | { kind: "stale"; rootId: string; subpath: string; absPath?: string }
  | { kind: "absent"; rootId: string; subpath: string }
  | { kind: "engine_off"; rootId: string; subpath: string };

export function liveSidecarTarget(
  bind: LocalBindResolution,
): SidecarTarget | null {
  return bind.kind === "live"
    ? { rootId: bind.rootId, subpath: bind.subpath }
    : null;
}

function emptySubpath(subpath: string | null | undefined): boolean {
  return !(subpath ?? "").replace(/^\/+|\/+$/g, "");
}

/**
 * 当前正经 sidecar 跑回合的会话 → 其 sidecar 目标（root + subpath + turnId）的映射。
 *
 * 一个挂起的交互（审批 / ask_user / plan_review）由统一入口 `resolveInteraction` 结算；
 * 它据此判断「本会话此刻是不是 sidecar 回合」——是则把结算改走 `window.sidecarApi.respond`
 * 回这条 stdio 链路（够到 sidecar 进程内的 `InteractionRegistry`），而非云端 HTTP（够不到）。
 * 子路径随目标一并记下，使 respond 能寻址到正确的（按 root+subpath 起的）sidecar 进程。
 * 由 `streamConversationViaSidecar` / sidecar attach 在回合起止时登记 / 注销。
 */
/** 活 sidecar 回合寻址（含 cancel 所需的 turnId）。 */
export interface ActiveSidecarTurn extends SidecarTarget {
  /** 活回合键（startTurn=`turnId`，resume=`messageId`）；多窗口后 attach 者赢时防双清。 */
  turnId?: string;
}

const activeSidecarTurns = new Map<string, ActiveSidecarTurn>();
/**
 * 回合结束后仍记住最近 sidecar 目标（含 turnId），供 harvest 重新 setActive，
 * 以及渲染侧流已拆、引擎可能仍在跑时的活干预（run-stop / 整轮 cancel）。
 * 只寻址，不问闲忙——生成中灯 / 发送门问 occupancy。
 */
const lastSidecarTargetByCid = new Map<string, ActiveSidecarTurn>();

/** 登记：该会话此刻在某 sidecar 目标（root + subpath）上跑回合（回合开始 / attach 时调）。 */
export function setActiveSidecarTurn(
  conversationId: string,
  rootId: string,
  subpath = "",
  turnId?: string,
): void {
  const target: ActiveSidecarTurn = { rootId, subpath, turnId };
  activeSidecarTurns.set(conversationId, target);
  lastSidecarTargetByCid.set(conversationId, target);
}

/**
 * 注销：该会话的 sidecar 回合已结束。
 * 若传入 `turnId`，仅当登记键匹配时才清——避免多窗口后 attach 者被前窗口 finally 误清。
 */
export function clearActiveSidecarTurn(
  conversationId: string,
  turnId?: string,
): void {
  if (turnId) {
    const cur = activeSidecarTurns.get(conversationId);
    if (cur?.turnId && cur.turnId !== turnId) return;
  }
  activeSidecarTurns.delete(conversationId);
}

/**
 * 该会话此刻在跑的 sidecar 目标（root + subpath + turnId）；非 sidecar 回合则 null。
 * ``turnId`` 供 ``stopConversation`` → ``sidecarApi.cancel`` 寻址。
 */
export function getActiveSidecarTarget(
  conversationId: string,
): ActiveSidecarTurn | null {
  return activeSidecarTurns.get(conversationId) ?? null;
}

/** 该会话最近一次 sidecar 目标（回合结束后仍在，含 turnId）；无则 null。 */
export function getLastSidecarTarget(
  conversationId: string,
): ActiveSidecarTurn | null {
  return lastSidecarTargetByCid.get(conversationId) ?? null;
}

/**
 * 活干预寻址：渲染侧流已拆（C1 断连 ≠ 取消）时活 map 为空，
 * 引擎仍可能在 sidecar 进程里跑。先活 map，再 last（含 turnId）。
 *
 * ``executionVia=sidecar`` 且两表都空时（例如渲染进程重载）才落到会话本地根。
 * 非 sidecar 不落到本地根，避免停令打进空 sidecar。
 */
export function resolveSidecarControlTarget(
  conversationId: string,
): ActiveSidecarTurn | null {
  return (
    getActiveSidecarTarget(conversationId) ??
    getLastSidecarTarget(conversationId)
  );
}

export async function resolveSidecarControlTargetForEngine(
  conversationId: string,
  executionVia: "sidecar" | null | undefined,
): Promise<ActiveSidecarTurn | SidecarTarget | null> {
  const mapped = resolveSidecarControlTarget(conversationId);
  if (mapped) return mapped;
  if (executionVia !== "sidecar") return null;
  return resolveConversationLocalTarget(conversationId);
}

/** 测试隔离：清空活回合与最近目标。 */
export function resetSidecarRoutingForTests(): void {
  activeSidecarTurns.clear();
  lastSidecarTargetByCid.clear();
}

function scratchFromWorkspaceCache(
  conversationId: string,
  folderId: string | null,
): { rootId: string | null; subpath: string } | null {
  const workspaces = queryClient.getQueryData<WorkspaceInfo[]>(
    workspaceKeys.list,
  );
  if (!workspaces) return null;
  if (folderId) {
    const folderWs = workspaces.find((w) => w.wsId === `folder:${folderId}`);
    if (folderWs) {
      return { rootId: folderWs.rootId, subpath: folderWs.subpath ?? "" };
    }
  }
  const ws = workspaces.find((w) => w.wsId === `conv:${conversationId}`);
  if (!ws) return null;
  return { rootId: ws.rootId, subpath: ws.subpath ?? "" };
}

/**
 * 解析会话的本地工作区绑定（三态），与 sidecar 寻址同构。
 *
 * 项目会话：继承 Folder 的 `local_root_id` + `local_subpath`。
 * 裸聊：执行环境绑定根下一律 `conversations/<id>`（空 subpath 契约路径）。
 * 根不在授权表 → absent（不走云）。空子路径且 `listRoots.missing` → stale（不走云）。
 */
export async function resolveLocalBind(
  conversationId: string,
): Promise<LocalBindResolution> {
  const conv = getConversations().find((c) => c.id === conversationId) ?? null;
  if (!conv) return { kind: "unbound" };

  if (conv.folderId) {
    const folder = getFolders().find((f) => f.id === conv.folderId);
    if (!folder || folder.mode !== "local" || !folder.localRootId) {
      return { kind: "unbound" };
    }
    const roots = await window.fsApi.listRoots();
    const root = roots.find((r) => r.id === folder.localRootId);
    if (!root) {
      return {
        kind: "absent",
        rootId: folder.localRootId,
        subpath: folder.localSubpath ?? "",
      };
    }
    const subpath = folder.localSubpath ?? "";
    if (emptySubpath(subpath) && root.missing) {
      return {
        kind: "stale",
        rootId: root.id,
        subpath: "",
        absPath: root.absPath,
      };
    }
    return { kind: "live", rootId: root.id, subpath };
  }

  const cached = scratchFromWorkspaceCache(conversationId, null);
  const rootId =
    cached?.rootId ?? conv.localRootId ?? conv.localContainerRootId ?? null;
  if (!rootId) return { kind: "unbound" };

  const cachedSub = (cached?.subpath ?? "").replace(/^\/+|\/+$/g, "");
  // 非空服务端子路径优先；空 subpath → 隔离契约路径（含显式绑定他根）。
  const subpath = cachedSub || bareConversationScratchSubpath(conversationId);

  const roots = await window.fsApi.listRoots();
  const root = roots.find((r) => r.id === rootId);
  if (!root) return { kind: "absent", rootId, subpath };
  if (emptySubpath(subpath) && root.missing) {
    return {
      kind: "stale",
      rootId,
      subpath: "",
      absPath: root.absPath,
    };
  }
  return { kind: "live", rootId, subpath };
}

/**
 * 解析会话的本地工作区目标（活绑定才返回）。死绑定 → null，**调用方不得据此走云**
 * （新回合用 {@link resolveNewTurnBind}；本函数给续跑 / 附件等「只要 live target」的面）。
 */
export async function resolveConversationLocalTarget(
  conversationId: string,
): Promise<SidecarTarget | null> {
  return liveSidecarTarget(await resolveLocalBind(conversationId));
}

/**
 * 新开回合绑定。
 *
 * 没有本机绑定 → unbound（云端对话）。有本机绑定但本地引擎接不住
 * （没有引擎 / 授权表没有这个 id / 目录已不在盘上）
 * → 对应的停发态，**不当成** unbound。
 */
export async function resolveNewTurnBind(
  conversationId: string,
): Promise<LocalBindResolution> {
  const bind = await resolveLocalBind(conversationId);
  if (bind.kind === "unbound") return bind;
  if (!hasLocalEngine()) {
    return {
      kind: "engine_off",
      rootId: bind.rootId,
      subpath: bind.subpath,
    };
  }
  return bind;
}

/** 本机文件夹回合在发送前停住时的原因与横幅。云端对话返回 null。 */
export function localBindSendBlock(
  bind: LocalBindResolution,
): { reason: string; message: string; rootId: string } | null {
  if (bind.kind === "stale") {
    return {
      reason: "root_stale",
      message: workspaceRootGoneMessage(bind.absPath),
      rootId: bind.rootId,
    };
  }
  if (bind.kind === "absent") {
    return {
      reason: "root_absent",
      message: workspaceRootAbsentMessage(),
      rootId: bind.rootId,
    };
  }
  if (bind.kind === "engine_off") {
    return {
      reason: "no_local_engine",
      message: localEngineOffMessage(),
      rootId: bind.rootId,
    };
  }
  return null;
}

/**
 * 解析**新开回合**应在其上跑 sidecar 的目标；不该走 sidecar 则 null（早退，不 probe / 不 spawn）。
 *
 * = 桌面有本地引擎，**且**该会话是活本机绑定。
 * 云项目 / 无本地绑定 → null（交回云链路）。
 * 死绑定、授权表没有这个 id、没有本地引擎，也是 null——**sendTurn 必须先看
 * {@link localBindSendBlock}，禁止把 null 当云**。
 *
 * 纯「新回合路由意图」，**不掺运行时健康**（探活由 `sendTurn` 收敛）。**续跑勿用本函数**：
 * `origin=sidecar` 须跟本地事实（{@link resolveLocalBind} /
 * {@link getActiveSidecarTarget}）——见 `runResume`。
 */
export async function resolveSidecarRoot(
  conversationId: string,
): Promise<SidecarTarget | null> {
  return liveSidecarTarget(await resolveNewTurnBind(conversationId));
}

/**
 * 该会话是否「能用本地引擎」（桌面端 + 本机绑定，含死绑定）。供 UI 判断某对话是否
 * 值得围绕本地引擎做状态展示 / 提示（如启动探活）。死绑定仍 true（芯片要给重新选择）。
 */
export async function canConversationUseSidecar(
  conversationId: string,
): Promise<boolean> {
  if (!hasLocalEngine()) return false;
  return (await resolveLocalBind(conversationId)).kind !== "unbound";
}
