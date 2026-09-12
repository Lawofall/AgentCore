import { Markdown } from "@/components/chat/Markdown";
import { Button } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { agentColorVar, agentGlyph } from "@/lib/agentIdentity";
import type {
  ContinuationChain,
  ContinuationVersion,
  Execution,
  RunNode,
} from "@/stores/execution";
import { useSidePanelStore } from "@/stores/sidePanel";
import { ChevronRight } from "lucide-react";
import { useState } from "react";
import { StatusDot } from "./ComparePane";
import { charCount, outputOf, placeholder, preview, versionTag } from "./cells";

/**
 * 版本链形态的纵览层（定向唤回「修订 vN」）——每条被改 worker 一条**版本轨**（全版纵览，读作一条
 * 从「原始 → 最新」的演进胶片），下挂一个**聚焦精读**面（默认读最新一版，全宽）。版本轨用**连接线**
 * 把逐版串成一条时间线（不再是并排孤立卡），并以**该 worker 的身份色**（{@link agentColorVar} by
 * role，与协作图 / 辩论 / 简报同源）统一着色 → 一眼读出「这是谁在改」。每帧标净字数变化（+N/-N，比裸
 * 字数更能说明「这版动了多少」）。对比模式下每帧变成可 pick 的 A/B 选取项（同链跨版本、或跨链），选中
 * 的两版喂给共享的 {@link import("./ComparePane").ComparePane}。纯投影：读同一份 {@link Execution}
 * （修订由 `run_started` 帧合成进来），live / 回放渲染一致。
 */
export function ContinuationOverview({
  chains,
  execution,
  messageId,
  compareMode,
  pair,
  onPick,
}: {
  chains: ContinuationChain[];
  execution: Execution;
  messageId: string;
  compareMode: boolean;
  /** 当前 A/B 选中的两个 `run.id`（display order：A 在前）。 */
  pair: [string, string];
  onPick: (runId: string) => void;
}) {
  return (
    <div className="space-y-5">
      {chains.map((chain) => (
        <ChainRow
          key={chain.originalId}
          chain={chain}
          execution={execution}
          messageId={messageId}
          compareMode={compareMode}
          pair={pair}
          onPick={onPick}
        />
      ))}
    </div>
  );
}

/** 一条被改 worker 的版本链：身份头（役色头像 + 役名 + 版数）+ 版本胶片轨（连接线串起逐版）。聚焦
 *  模式下焦点面读一版；对比模式下轨上版本帧可 pick 进 turn 级 A/B 对。 */
function ChainRow({
  chain,
  execution,
  messageId,
  compareMode,
  pair,
  onPick,
}: {
  chain: ContinuationChain;
  execution: Execution;
  messageId: string;
  compareMode: boolean;
  pair: [string, string];
  onPick: (runId: string) => void;
}) {
  const original = chain.versions[0].run;
  const agent = execution.agents.find((a) => a.id === original.agentId);
  const role = agent?.role ?? original.agentId;
  const colorVar = agentColorVar(role);
  const versions = chain.versions;
  const latest = versions[versions.length - 1].version;

  const [focus, setFocus] = useState<number>(latest);

  const byVersion = (v: number): ContinuationVersion =>
    versions.find((x) => x.version === v) ?? versions[0];

  // 逐版净字数（供每帧标 +N/-N 相对上一版的增删；比裸字数更说明「这版改了多少」）。
  const counts = versions.map((v) => charCount(outputOf(execution, v.run)));

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <span
          className="flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
          style={{
            color: colorVar,
            backgroundColor: `color-mix(in oklch, ${colorVar} 18%, transparent)`,
          }}
          aria-hidden
        >
          {agentGlyph(role)}
        </span>
        <span className="truncate text-sm font-semibold text-foreground">
          {role}
        </span>
        <span className="text-xs text-muted-foreground">
          {versions.length} 版演进
        </span>
      </div>

      <div className="flex items-stretch overflow-x-auto pb-1">
        {versions.map((version, i) => {
          const badge =
            version.run.id === pair[0]
              ? "A"
              : version.run.id === pair[1]
                ? "B"
                : null;
          return (
            <div key={version.run.id} className="flex items-stretch">
              {i > 0 && <VersionConnector />}
              <VersionChip
                version={version}
                latest={latest}
                colorVar={colorVar}
                chars={counts[i]}
                delta={i > 0 ? counts[i] - counts[i - 1] : null}
                output={outputOf(execution, version.run)}
                active={compareMode ? badge != null : version.version === focus}
                badge={compareMode ? badge : null}
                onPick={() =>
                  compareMode
                    ? onPick(version.run.id)
                    : setFocus(version.version)
                }
              />
            </div>
          );
        })}
      </div>

      {!compareMode && (
        <FocusPane
          version={byVersion(focus)}
          latest={latest}
          role={role}
          colorVar={colorVar}
          messageId={messageId}
          output={outputOf(execution, byVersion(focus).run)}
        />
      )}
    </div>
  );
}

/** 版本帧之间的连接线（原始 → 最新 演进方向）：一段细线 + 雪佛龙，把逐版串成一条胶片时间线。 */
function VersionConnector() {
  return (
    <div className="flex w-7 shrink-0 items-center justify-center text-muted-foreground/50">
      <span className="h-px w-2 bg-border" />
      <ChevronRight size={14} className="shrink-0" />
    </div>
  );
}

/** 胶片轨上的一帧：顶缘役色描边 + [状态点 · vN · 原始/最新 · A/B] + 净字数(±Δ) + 两行预览。选中
 *  （焦点、或对比 A/B 槽）改品牌蓝环 + 蓝底；A/B 槽带徽章。 */
function VersionChip({
  version,
  latest,
  colorVar,
  chars,
  delta,
  output,
  active,
  badge,
  onPick,
}: {
  version: ContinuationVersion;
  latest: number;
  colorVar: string;
  chars: number;
  /** 相对上一版的净字数增删（v1 为 null）。 */
  delta: number | null;
  output: string;
  active: boolean;
  badge: "A" | "B" | null;
  onPick: () => void;
}) {
  const { run } = version;
  const tag = versionTag(version.version, latest);

  return (
    <button
      type="button"
      onClick={onPick}
      className={`flex w-44 shrink-0 flex-col gap-1 rounded-xl border p-2.5 text-left transition-colors ${
        active
          ? "border-primary bg-primary/5 ring-1 ring-primary"
          : "border-border bg-card hover:border-muted-foreground/40"
      }`}
      style={
        active ? undefined : { borderTopColor: colorVar, borderTopWidth: 2 }
      }
    >
      <span className="flex items-center gap-1.5">
        <StatusDot status={run.status} />
        <span className="text-xs font-semibold text-foreground">
          v{version.version}
        </span>
        {tag && (
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
            {tag}
          </span>
        )}
        <span className="flex-1" />
        {badge && (
          <span className="rounded bg-primary px-1 text-xs font-semibold text-primary-foreground">
            {badge}
          </span>
        )}
      </span>
      <span className="flex items-baseline gap-1.5 text-xs text-muted-foreground">
        {output ? (
          <>
            {chars} 字
            {delta !== null && delta !== 0 && (
              <span
                className={delta > 0 ? "text-success" : "text-muted-foreground"}
              >
                {delta > 0 ? `+${delta}` : delta}
              </span>
            )}
          </>
        ) : (
          placeholder(run)
        )}
      </span>
      {output && (
        <span className="line-clamp-2 text-xs leading-snug text-muted-foreground/80">
          {preview(output)}
        </span>
      )}
    </button>
  );
}

/** 全宽读一版：役色描边 + 头部（役色版号点 + vN + 原始/最新 + 钻完整 run 详情）+ 产出（限高 60vh 可滚）。 */
function FocusPane({
  version,
  latest,
  role,
  colorVar,
  messageId,
  output,
}: {
  version: ContinuationVersion;
  latest: number;
  role: string;
  colorVar: string;
  messageId: string;
  output: string;
}) {
  const { run } = version;

  return (
    <div
      className="overflow-hidden rounded-xl border border-border bg-card"
      style={{ borderTopColor: colorVar, borderTopWidth: 2 }}
    >
      <VersionHeader
        version={version.version}
        latest={latest}
        run={run}
        role={role}
        colorVar={colorVar}
        messageId={messageId}
      />
      <div className="p-3">
        {output ? (
          <div className="max-h-[60vh] overflow-y-auto text-sm">
            <Markdown content={output} />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">{placeholder(run)}</p>
        )}
      </div>
    </div>
  );
}

/** 焦点面头部：役色版号点 + vN + 原始/最新 tag + 钻完整 run 详情。 */
function VersionHeader({
  version,
  latest,
  run,
  role,
  colorVar,
  messageId,
}: {
  version: number;
  latest: number;
  run: RunNode;
  role: string;
  colorVar: string;
  messageId: string;
}) {
  const showRunDetail = useSidePanelStore((s) => s.showRunDetail);
  const tag = versionTag(version, latest);
  return (
    <div className="flex items-center gap-1.5 border-b border-border px-3 py-2">
      <span
        className="flex size-5 shrink-0 items-center justify-center rounded-full text-xs font-semibold"
        style={{
          color: colorVar,
          backgroundColor: `color-mix(in oklch, ${colorVar} 18%, transparent)`,
        }}
        aria-hidden
      >
        v{version}
      </span>
      {tag && <span className="text-xs text-muted-foreground">{tag}</span>}
      <span className="flex-1" />
      <SimpleTooltip label="查看完整产出">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => showRunDetail(messageId, run.id, role)}
          className="h-6 gap-1 px-2 text-muted-foreground"
        >
          完整产出
          <ChevronRight size={12} />
        </Button>
      </SimpleTooltip>
    </div>
  );
}
