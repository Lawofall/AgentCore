import { Button } from "@/components/ui";
import {
  type Execution,
  type RevisionChain,
  revisionChains,
} from "@/stores/execution";
import { Columns2, X } from "lucide-react";
import { useMemo, useState } from "react";
import { ComparePane } from "./ComparePane";
import { RevisionOverview } from "./RevisionOverview";
import { type ResolvedCell, revisionCells } from "./cells";

/**
 * 「对比」透镜（协作图与双视图UX.md §六 两个入口：聊天内嵌 ⇄ 全屏放大）—— **定向唤回修订**的版本对比：一层**版本轨纵览**
 * （{@link RevisionOverview}：每条被改 worker 链的 `v1…vN` 胶片轨 + 聚焦精读）+ 一层**内容自适应
 * 精读**（点任意两格 → 同一个 {@link ComparePane}：读作编辑给真·文本 diff、否则 2-up 渲染）。二者共享
 * 格子外壳、pick-two 选择、对比面。
 *
 * 挂载于全屏回合详情页的「对比」视图（{@link import("../../../pages/TurnDetailPage").TurnDetailPage}）——仅服务非辩论修订：辩论回合的对比已并入群聊「并排」布局（§4.1b「2026-07 delta」），不再进此透镜。纯投影：读同一份 {@link Execution}，live / 回放渲染一致。
 */
export function TurnCompare({
  execution,
  messageId,
  initialPair,
}: {
  execution: Execution;
  messageId: string;
  /** 深链预选的 A/B 版本对（run.id）——如从侧面板版本链点 vN 直达 vN-1×vN diff。仅当两个 id
   * 都落在本回合可对比单元里才采纳（并直接进对比模式）；否则退回自然默认对。 */
  initialPair?: [string, string];
}) {
  const chains = useMemo(() => revisionChains(execution), [execution]);

  // 可选取单元（display order）——供 A/B pair 解析与默认对定序共用同一顺序。
  const cells = useMemo<ResolvedCell[]>(
    () => (chains.length > 0 ? revisionCells(execution, chains) : []),
    [execution, chains],
  );

  // 深链预选对：两个 id 都在本回合可对比单元里、且互异，才采纳（进对比模式并 seed 该对）。
  const deepLinked =
    initialPair != null &&
    initialPair[0] !== initialPair[1] &&
    cells.some((c) => c.run.id === initialPair[0]) &&
    cells.some((c) => c.run.id === initialPair[1]);
  // 对比模式：深链预选对 → 直接进对比；否则单链恰 2 版的经典修订（原始 × 最新）也直接进对比开 diff；
  // 其余默认关。
  const [compareMode, setCompareMode] = useState<boolean>(
    deepLinked || (chains.length === 1 && chains[0].versions.length === 2),
  );
  const [pair, setPair] = useState<[string, string]>(() =>
    deepLinked && initialPair ? initialPair : defaultPair(cells, chains),
  );

  if (chains.length === 0) return null;

  const pick = (runId: string) =>
    // 保留最近点的两个不同格（新点 → B / 右槽）。
    setPair(([a, b]) => (a === runId || b === runId ? [a, b] : [b, runId]));

  const toggleCompare = () =>
    setCompareMode((on) => {
      const next = !on;
      if (next) setPair(defaultPair(cells, chains));
      return next;
    });

  // display order 定序：同数组里下标小者为 A（同链低版本在前），对比面与格子徽章一致。
  const byId = new Map(cells.map((c) => [c.run.id, c]));
  const idxOf = (id: string) => cells.findIndex((c) => c.run.id === id);
  const ordered: [string, string] =
    idxOf(pair[0]) <= idxOf(pair[1]) ? [pair[0], pair[1]] : [pair[1], pair[0]];
  const ra = byId.get(ordered[0]) ?? null;
  const rb = byId.get(ordered[1]) ?? null;

  const summary =
    chains.length > 1
      ? `${chains.length} 方 · 共 ${chains.reduce((n, c) => n + c.versions.length, 0)} 版`
      : `${chains[0]?.versions.length ?? 0} 版`;
  const hint = compareMode ? "点版本卡选 A / B（可跨方）" : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted-foreground">{summary}</span>
        {hint && (
          <span className="text-xs text-muted-foreground/70">{hint}</span>
        )}
        <div className="flex-1" />
        <Button
          variant={compareMode ? "primary" : "neutral"}
          size="sm"
          onClick={toggleCompare}
        >
          {compareMode ? <X size={13} /> : <Columns2 size={13} />}
          {compareMode ? "退出对比" : "对比两版"}
        </Button>
      </div>

      <RevisionOverview
        chains={chains}
        execution={execution}
        messageId={messageId}
        compareMode={compareMode}
        pair={ordered}
        onPick={pick}
      />

      {compareMode && <ComparePane a={ra} b={rb} messageId={messageId} />}
    </div>
  );
}

/**
 * 进入对比时的预选对（修订）：≥2 链 → 各取前两链的最新（撰写员终稿 × 审阅员终稿…）；
 * 单链 → 原始 × 最新（经典 diff）。
 */
function defaultPair(
  cells: ResolvedCell[],
  chains: RevisionChain[],
): [string, string] {
  if (cells.length === 0) return ["", ""];
  const latestRun = (c: RevisionChain) =>
    c.versions[c.versions.length - 1].run.id;
  if (chains.length >= 2) return [latestRun(chains[0]), latestRun(chains[1])];
  const c = chains[0];
  return [c.versions[0].run.id, latestRun(c)];
}
