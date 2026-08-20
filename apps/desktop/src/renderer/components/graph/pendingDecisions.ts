/**
 * 图头「待你拍板」行动条的数据层（批 R3 决策 3）。
 *
 * 聚合一个回合（execution）里全部**待你拍板**：检查点 / 阻塞升级（node 级，散在
 * 节点角标的那批）+ 热闸交互卡（execution 级；store 切片 = `hot && pausesTurn`，
 * 标题从 {@link hotGateKindTitle} 取，与 {@link INTERACTION_CARD_NAME} 同源；今日成员是审批 →「工具审批」）。行动条据此显示
 * 「待你拍板 N」并逐条定位——node 级直达对应节点（折叠幕内先聚焦幕再居中），
 * execution 级锚到团队代表节点 / 整图。
 *
 * 这不是 `attention` 全集（不含冷卡 / 挂问 / 幕推进卡）——图头只导航图上已有
 * 的拍板入口，真正的卡仍归聊天流。
 *
 * 纯函数、无 React / 无 store——{@link useGraphPendingDecisions} 从交互 store 取
 * pending 交互引用后调本函数；单测直接钉聚合与锚定逻辑。**不建卡**：行动条只做
 * 导航，真正的拍板卡仍归聊天流既有面（不与其重复）。
 */

import { escalationRowKindLabel } from "@/components/graph/agentNode/shared";
import { resolveCaptainSinkId, workerRunsOf } from "@/components/graph/helpers";
import type { Execution, RunNode } from "@/stores/execution";
import {
  type HotGateInteractionKind,
  hotGateKindTitle,
} from "@/stores/interactions/registry";

export type GraphPendingKind =
  | "escalation"
  | "checkpoint"
  | HotGateInteractionKind;

export interface GraphPendingDecision {
  /** 稳定去重键。 */
  id: string;
  kind: GraphPendingKind;
  /** 定位目标 run 节点；null = execution 级（定位退回整图 fit）。 */
  runId: string | null;
  /** 目标所属幕 actId（折叠幕内先聚焦该幕再居中）；null = 未知 / 不适用。 */
  actId: string | null;
  /** 主标题：node 级用角色名；execution 级用热闸标题（与 {@link hotGateKindTitle} 同源）。 */
  title: string;
  /** 一行原因：待放行 / 待你拍板（缺输入）/ 放行开工 …。 */
  detail: string;
}

/** 由 store 派生的 pending 热闸交互引用（切片 = `hot && pausesTurn`）。 */
export interface PendingInteractionRef {
  kind: string;
  id: string;
}

/**
 * Cheap Live signature for graph action-bar pending decisions.
 * Streaming output must NOT change this; node escalate / checkpoint must.
 */
export function graphPendingDecisionsLiveSig(
  execution: Execution | null | undefined,
): string {
  if (!execution) return "";
  const parts: string[] = [];
  for (const r of execution.runs) {
    let pendingEsc = 0;
    for (const e of r.escalations) {
      if (e.status === "pending") pendingEsc++;
    }
    const cp = r.checkpoint?.status === "pending" ? "1" : "0";
    if (pendingEsc === 0 && cp === "0") continue;
    parts.push(`${r.id}:${pendingEsc}:${cp}`);
  }
  return parts.join("|");
}

function escalationKindTag(esc: RunNode["escalations"][number]): string {
  const label = escalationRowKindLabel(esc);
  return label ? `（${label}）` : "";
}

/**
 * 聚合一个 execution 的全部待拍板。顺序：先按 run 顺序收 node 级（升级 → 检查点），
 * 再收 execution 级热闸——与「先扫节点、再看图头热闸」的阅读序一致。
 */
export function collectGraphPendingDecisions(
  execution: Execution | null | undefined,
  interactions: readonly PendingInteractionRef[] = [],
): GraphPendingDecision[] {
  if (!execution) return [];
  const captainId = resolveCaptainSinkId(execution.runs);
  const roleOf = (r: RunNode): string =>
    execution.agents.find((a) => a.id === r.agentId)?.role ??
    r.role ??
    r.agentId;

  const out: GraphPendingDecision[] = [];
  for (const r of workerRunsOf(execution.runs)) {
    // 与节点「待你拍板」角标同口径：全部 pending 升级都计（含 CEO 仲裁中，节点角标亦显）。
    for (const [i, e] of r.escalations.entries()) {
      if (e.status !== "pending") continue;
      out.push({
        id: `esc:${e.id ?? `${r.id}:${i}`}`,
        kind: "escalation",
        runId: r.id,
        actId: r.actId ?? "act-1",
        title: roleOf(r),
        detail: `待你拍板${escalationKindTag(e)}`,
      });
    }
    if (r.checkpoint?.status === "pending") {
      out.push({
        id: `cp:${r.id}`,
        kind: "checkpoint",
        runId: r.id,
        actId: r.actId ?? "act-1",
        title: roleOf(r),
        detail: "待放行",
      });
    }
  }

  // execution 级热闸：锚到团队代表节点。标题按 kind 取，不一律写成「工具审批」。
  for (const it of interactions) {
    out.push({
      id: `${it.kind}:${it.id}`,
      kind: it.kind as GraphPendingKind,
      runId: captainId,
      actId: null,
      title: hotGateKindTitle(it.kind),
      detail: "待放行",
    });
  }
  return out;
}
