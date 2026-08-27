import type { SidecarEventPush } from "@shared/sidecar-contract";

/**
 * Sidecar live 事件单例泵（每 turn 单事件泵）。
 *
 * 根因：`sidecarApi.onEvent` 每次调用都 `ipcRenderer.on`，`runSidecarTurn` 与
 * `attachSidecarTurn` 可叠多个 listener → 同一 `content_delta` 进 fold 两次（叠字）。
 *
 * 契约：App 生命周期对 `sidecar:event` **只订一次**；回合方 **claim**
 * `(conversationId, turnId)` 的 sink。
 *
 * - **同 turnId**（或任一方 `null` 通配）：新 claim 驱逐旧 owner（防叠字 / attach 接管）。
 * - **不同具体 turnId**：D9 冷 resume × live 共存——两路各收各的事件，互不驱逐。
 * - `resume_deferred`：会话级 EPHEMERAL，扇出到该会话全部 owner（turnId 不一致也能进
 *   `dispatchSSEEvent` → `markResumeDeferred`）。
 * - **未认领 turnId**：交给 `setUnclaimedSidecarTurnHandler`（若已注册）；
 *   无 handler 则丢弃。禁止在泵内新造 SSE。
 *
 * 禁止在 fold/contentBuffer 对相同 delta 去重。
 */

export type SidecarTurnSink = (push: SidecarEventPush) => void;

export interface SidecarTurnClaim {
  readonly token: string;
  readonly conversationId: string;
  /** `null` = 尚未收窄（attach 在 IPC 返回前接受该会话任意 turn）。 */
  readonly turnId: string | null;
  /** Attach 拿到 `turnId` 后收窄过滤；非 owner 时 no-op。 */
  setTurnId(turnId: string): void;
  /** 释放本 claim；仅当仍是当前 owner 时清除登记。 */
  release(): void;
  /** 是否仍占有该会话的 sink（本 token）。 */
  isOwner(): boolean;
}

type Owner = {
  token: string;
  conversationId: string;
  turnId: string | null;
  sink: SidecarTurnSink;
  onRevoked?: () => void;
};

/** conversationId → 该会话全部 owner（D9 可多于一个具体 turn）。 */
const ownersByConv = new Map<string, Owner[]>();

let installed = false;
let unsubscribeIpc: (() => void) | null = null;

/**
 * 订阅 `sidecar:event`（幂等）；在 renderer 启动时调一次。
 * 非桌面 / 未注入 `sidecarApi` 时 no-op。
 */
export function installSidecarEventPump(): void {
  if (installed) return;
  if (typeof window === "undefined" || !window.sidecarApi?.onEvent) return;
  installed = true;
  unsubscribeIpc = window.sidecarApi.onEvent(routePush);
}

function listOwners(conversationId: string): Owner[] {
  return ownersByConv.get(conversationId) ?? [];
}

function setOwners(conversationId: string, next: Owner[]): void {
  if (next.length === 0) ownersByConv.delete(conversationId);
  else ownersByConv.set(conversationId, next);
}

function revokeOwner(owner: Owner): void {
  owner.onRevoked?.();
}

/** Owners that a new claim with `turnId` must displace. */
function ownersToRevoke(existing: Owner[], turnId: string | null): Owner[] {
  if (turnId === null) {
    // Attach / preheat：整会话独占，清掉所有既有 claim。
    return [...existing];
  }
  return existing.filter((o) => o.turnId === null || o.turnId === turnId);
}

type UnclaimedSidecarTurnHandler = (push: SidecarEventPush) => boolean;

let unclaimedHandler: UnclaimedSidecarTurnHandler | null = null;

/**
 * 未认领 turnId 的可选兜底。返回 true 表示已 claim，泵应再投递本帧。
 * 测试 reset 会清掉。
 */
export function setUnclaimedSidecarTurnHandler(
  handler: UnclaimedSidecarTurnHandler | null,
): void {
  unclaimedHandler = handler;
}

function deliverToMatchingOwners(
  push: SidecarEventPush,
  owners: Owner[],
): boolean {
  let delivered = false;
  for (const owner of owners) {
    if (owner.turnId !== null && owner.turnId !== push.turnId) continue;
    owner.sink(push);
    delivered = true;
  }
  return delivered;
}

function routePush(push: SidecarEventPush): void {
  const owners = listOwners(push.conversationId);

  const eventType =
    push.event && typeof push.event === "object"
      ? String((push.event as { type?: unknown }).type ?? "")
      : "";
  // 会话级 EPHEMERAL：扇出到全部 owner（冷续跑 claim 与 live claim turnId 不同）。
  if (eventType === "resume_deferred") {
    const seen = new Set<string>();
    for (const owner of owners) {
      if (seen.has(owner.token)) continue;
      seen.add(owner.token);
      owner.sink(push);
    }
    return;
  }

  if (deliverToMatchingOwners(push, owners)) return;
  if (!unclaimedHandler?.(push)) return;
  deliverToMatchingOwners(push, listOwners(push.conversationId));
}

/**
 * Claim 某会话 sidecar live 的 sink。
 *
 * @param turnId 已知则按 turn 过滤；`null` 表示 attach 预热（该会话任意 turn）。
 * @param onRevoked 被同 turn / 通配 claim 顶替时回调（旧泵应停 fold / resolve）。
 */
export function claimSidecarTurnSink(
  conversationId: string,
  turnId: string | null,
  sink: SidecarTurnSink,
  opts?: { onRevoked?: () => void },
): SidecarTurnClaim {
  // 单测 / 未走 main.tsx 时惰性安装，保证 claim 路径仍只有一条 IPC 订阅。
  installSidecarEventPump();

  const existing = listOwners(conversationId);
  const doomed = ownersToRevoke(existing, turnId);
  const doomedTokens = new Set(doomed.map((o) => o.token));
  for (const o of doomed) revokeOwner(o);

  const token = crypto.randomUUID();
  const owner: Owner = {
    token,
    conversationId,
    turnId,
    sink,
    onRevoked: opts?.onRevoked,
  };
  const kept = existing.filter((o) => !doomedTokens.has(o.token));
  setOwners(conversationId, [...kept, owner]);

  return {
    get token() {
      return token;
    },
    get conversationId() {
      return conversationId;
    },
    get turnId() {
      return owner.turnId;
    },
    setTurnId(next: string) {
      const cur = listOwners(conversationId).find((o) => o.token === token);
      if (!cur) return;
      // 收窄到具体 turn：驱逐同 turn 的其它 owner（含仍通配的 attach 竞态）。
      const rivals = listOwners(conversationId).filter(
        (o) => o.token !== token && (o.turnId === null || o.turnId === next),
      );
      for (const r of rivals) revokeOwner(r);
      const rivalTokens = new Set(rivals.map((o) => o.token));
      cur.turnId = next;
      setOwners(
        conversationId,
        listOwners(conversationId).filter(
          (o) => o.token === token || !rivalTokens.has(o.token),
        ),
      );
    },
    release() {
      const cur = listOwners(conversationId);
      if (!cur.some((o) => o.token === token)) return;
      setOwners(
        conversationId,
        cur.filter((o) => o.token !== token),
      );
    },
    isOwner() {
      return listOwners(conversationId).some((o) => o.token === token);
    },
  };
}

/** 测试隔离：清空 owner 并卸掉 IPC 订阅。 */
export function resetSidecarEventPumpForTests(): void {
  ownersByConv.clear();
  unsubscribeIpc?.();
  unsubscribeIpc = null;
  installed = false;
  unclaimedHandler = null;
}
