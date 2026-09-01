import { getConversations } from "@/hooks/useConversations";
import { getFolders } from "@/hooks/useFolders";
import { hasLocalEngine } from "@/lib/capabilities";
import { queryClient } from "@/lib/queryClient";
import { workspaceKeys } from "@/lib/queryKeys";
import { bareConversationScratchSubpath } from "@/services/bareScratchPath";
import type { WorkspaceInfo } from "@/services/workspaces";
import { useUIStore } from "@/stores/ui";

/**
 * 会话路由判定：一个回合该走本地 sidecar，还是云端 SSE。
 *
 * 双模式工作区 §7.2：本机传统（`mode=local` + 本机根可用）新开回合**默认同侧** sidecar；
 * 云协作永不 sidecar。过桥仅探活失败等机制兜底（见 `turns.sendTurn`），不当默认。
 * 通用·进阶「允许本机执行」显式关 = 强制走云；unset / 默认关**不**挡本机传统同侧。
 *
 * 续跑例外：`origin=sidecar` / 已有本机活回合须跟本地事实（{@link resolveConversationLocalTarget}
 * / {@link getActiveSidecarTarget}），忽略强制关——本机帧云端没有。
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
 * 云过桥（``cloud_bridge``）不得走本地根，否则停令打进空 sidecar、云上队员不停。
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
  executionVia: "sidecar" | "cloud_bridge" | null | undefined,
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

/**
 * 用户是否显式强制关闭本机执行（进阶开关「允许本机执行」关 → 偏好 `off`）。
 * unset / 默认关**不算**强制关——本机传统仍可默认同侧。web 无本地引擎时亦视为不可用。
 */
export function isSidecarForceOff(): boolean {
  return useUIStore.getState().sidecarPreference === "off";
}

/**
 * 桌面本地引擎能力面是否可用（有引擎 + 未强制关）。
 * 新开回合路由见 {@link resolveSidecarRoot}（另要求本机绑定）；本函数供过桥 reason 等。
 * web 恒 false。
 */
export function isSidecarEnabled(): boolean {
  return hasLocalEngine() && !isSidecarForceOff();
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
 * 解析会话的本地工作区目标（容器根 + scratch / 项目子路径），与 sidecar 寻址同构。
 *
 * 项目会话：继承 Folder 的 `local_root_id` + `local_subpath`。
 * 裸聊：执行环境绑定根下一律 `conversations/<id>`（空 subpath 契约路径；
 * 不把所选根当工作区根打开全文）。根不在本机 → null（§八 降级走云）。
 */
export async function resolveConversationLocalTarget(
  conversationId: string,
): Promise<SidecarTarget | null> {
  const conv = getConversations().find((c) => c.id === conversationId) ?? null;
  if (!conv) return null;

  if (conv.folderId) {
    const folder = getFolders().find((f) => f.id === conv.folderId);
    if (!folder || folder.mode !== "local" || !folder.localRootId) return null;
    const roots = await window.fsApi.listRoots();
    if (!roots.some((r) => r.id === folder.localRootId)) return null;
    return {
      rootId: folder.localRootId,
      subpath: folder.localSubpath ?? "",
    };
  }

  const cached = scratchFromWorkspaceCache(conversationId, null);
  const rootId =
    cached?.rootId ?? conv.localRootId ?? conv.localContainerRootId ?? null;
  if (!rootId) return null;

  const cachedSub = (cached?.subpath ?? "").replace(/^\/+|\/+$/g, "");
  // 非空服务端子路径优先；空 subpath → 隔离契约路径（含显式绑定他根）。
  const subpath = cachedSub || bareConversationScratchSubpath(conversationId);

  const roots = await window.fsApi.listRoots();
  if (!roots.some((r) => r.id === rootId)) return null;
  return { rootId, subpath };
}

/**
 * 解析**新开回合**应在其上跑 sidecar 的目标；不该走 sidecar 则 null（早退，不 probe / 不 spawn）。
 *
 * = 桌面有本地引擎、用户未显式强制关（{@link isSidecarForceOff}），**且**该会话有本机绑定
 * （{@link resolveConversationLocalTarget}：`mode=local` 项目 / 本机根在盘上）。
 * 云项目 / 无本地绑定 / 根不在本机 / 显式强制关 → null（交回云链路）。
 * **不**因 unset→`SIDECAR_DEFAULT_ENABLED=false` 早退。
 *
 * 纯「新回合路由意图」，**不掺运行时健康**（探活由 `sendTurn` 收敛）。**续跑勿用本函数**：
 * `origin=sidecar` 须跟本地事实（{@link resolveConversationLocalTarget} /
 * {@link getActiveSidecarTarget}），忽略强制关——见 `runResume`。
 */
export async function resolveSidecarRoot(
  conversationId: string,
): Promise<SidecarTarget | null> {
  if (!hasLocalEngine() || isSidecarForceOff()) return null;
  return resolveConversationLocalTarget(conversationId);
}

/**
 * 该会话是否「能用本地引擎」（桌面端 + 绑定本机存在的本地根），**不看强制关**——与
 * {@link isSidecarForceOff} / {@link isSidecarEnabled} 正交的公共查询。供 UI 判断某对话是否
 * 值得围绕本地引擎做状态展示 / 提示（如启动探活），只有真能走 sidecar 的对话
 * （local 模式 + 根在本机）才返回 true。
 */
export async function canConversationUseSidecar(
  conversationId: string,
): Promise<boolean> {
  if (!hasLocalEngine()) return false;
  return (await resolveConversationLocalTarget(conversationId)) !== null;
}
