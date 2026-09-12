import type { ContinuationChain, Execution, RunNode } from "@/stores/execution";

/**
 * 「对比」透镜的可选取单元（{@link import("./TurnCompare").TurnCompare}）——把定向唤回修订的
 * 版本链归一成可 pick 的格子，喂给共享的精读对比面（{@link import("./ComparePane").ComparePane}）。
 * 每个格子解析到唯一的版本 `run`，A/B 选择即按 `run.id` 记（跨链 / 跨版本自由取两个）。
 */
export interface ResolvedCell {
  run: RunNode;
  /** 产出全文（拼接流式分片），产出落地前为空串。 */
  output: string;
  /** A/B 标签展示的角色名（版本链的 worker 角色）。 */
  role: string;
  /** 一眼定位这格是「哪一个」：版本链为 `v2`。 */
  label: string;
  /** 次级标签（版本链的 原始/最新）。 */
  tag: string | null;
}

/** 一个 run 的渲染产出文本（拼接流式分片），产出落地前为空串。 */
export function outputOf(execution: Execution, run: RunNode): string {
  const agent = execution.agents.find((a) => a.id === run.agentId);
  return agent ? agent.outputChunks.join("") : "";
}

/** 非空白字符数——读作 CJK 也合适的「字数」代理。 */
export function charCount(s: string): number {
  return s.replace(/\s+/g, "").length;
}

/** 版本卡的一眼预览行。 */
export function preview(s: string): string {
  return s.replace(/\s+/g, " ").trim().slice(0, 140);
}

/** 一格尚无产出文本时的占位。 */
export function placeholder(run: RunNode): string {
  if (run.status === "running") return "正在生成…";
  if (run.status === "failed") return run.error ?? "该版本执行失败。";
  if (run.status === "cancelled") return "已停止。";
  return "（暂无输出）";
}

/** 一个版本在链里的角色：v1 是原始、末版是最新、其余不标。 */
export function versionTag(version: number, latest: number): string | null {
  if (version === 1) return "原始";
  if (version === latest) return "最新";
  return null;
}

/**
 * 廉价 O(n) 判断 `b` 读起来是否像 `a` 的一次**编辑**（定向唤回定点修订：同一交付物、微调），
 * 而非整段重写（两个不同角色的产出、或彻底重写）——共享的长前缀+后缀 + 相近长度。门住自动文本
 * diff：只在读作编辑处开，绝不在整段重写处开（那时字符 diff 是噪音）。无 LCS，仅端锚公共段。
 */
export function looksLikeEdit(a: string, b: string): boolean {
  if (!a || !b) return false;
  const max = Math.max(a.length, b.length);
  const min = Math.min(a.length, b.length);
  if (min / max < 0.4) return false; // 一方约为另一方 2.5x → 多半是重写
  let pre = 0;
  while (pre < min && a[pre] === b[pre]) pre++;
  let suf = 0;
  while (suf < min - pre && a[a.length - 1 - suf] === b[b.length - 1 - suf])
    suf++;
  return (pre + suf) / max >= 0.25; // 首尾未动 ≥¼ 文本
}

/**
 * 版本链形态的可选取单元：每条链（被改 worker）的 v1 原始 + 每次续写 vN，按版本序展开成一排格子。
 * 供 {@link import("./ContinuationOverview").ContinuationOverview} 渲染与 {@link import("./TurnCompare").TurnCompare}
 * 的 pair 解析共用同一份顺序（display order），A/B 定序即按此数组下标。
 */
export function continuationCells(
  execution: Execution,
  chains: ContinuationChain[],
): ResolvedCell[] {
  const out: ResolvedCell[] = [];
  for (const chain of chains) {
    const original = chain.versions[0].run;
    const agent = execution.agents.find((a) => a.id === original.agentId);
    const role = agent?.role ?? original.agentId;
    const latest = chain.versions[chain.versions.length - 1].version;
    for (const v of chain.versions) {
      out.push({
        run: v.run,
        output: outputOf(execution, v.run),
        role,
        label: `v${v.version}`,
        tag: versionTag(v.version, latest),
      });
    }
  }
  return out;
}
