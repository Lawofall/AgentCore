import {
  graphBadgeMuted,
  graphBadgeMutedPlain,
} from "@/components/ui/tone-presets";
import {
  Bot,
  CheckCircle2,
  Loader2,
  PencilLine,
  Sparkles,
  UserRound,
  XCircle,
} from "lucide-react";
import type { ReactNode } from "react";

/** 圆形头像底（与节点左上角图标同构）。 */
function IconChip({ children }: { children: ReactNode }) {
  return (
    <span className="flex size-7 items-center justify-center rounded-full bg-muted">
      {children}
    </span>
  );
}

function StanceGlyph({
  glyph,
  colorVar,
}: {
  glyph: string;
  colorVar: string;
}) {
  return (
    <span
      className="flex size-6 items-center justify-center rounded-full text-xs font-semibold"
      style={{
        backgroundColor: `color-mix(in oklab, ${colorVar} 18%, transparent)`,
        color: colorVar,
      }}
    >
      {glyph}
    </span>
  );
}

/** 连线样本：复刻 StepEdge 的描边语义（实线/虚线/点线 + 运行中 primary 粒子）。 */
function EdgeSample({
  variant,
}: {
  variant: "dep" | "delegate" | "continuation" | "running";
}) {
  const stroke =
    variant === "running" ? "var(--primary)" : "var(--muted-foreground)";
  const dash =
    variant === "continuation"
      ? "2 4"
      : variant === "delegate"
        ? "5 4"
        : undefined;
  const opacity = variant === "running" ? 1 : variant === "dep" ? 0.6 : 0.45;
  const label = {
    dep: "实线依赖边",
    delegate: "虚线委派边",
    continuation: "点线接续边",
    running: "运行中粒子边",
  }[variant];
  return (
    <svg
      width="48"
      height="12"
      viewBox="0 0 48 12"
      role="img"
      aria-label={label}
    >
      <line
        x1="2"
        y1="6"
        x2="46"
        y2="6"
        stroke={stroke}
        strokeWidth="2"
        strokeDasharray={dash}
        opacity={opacity}
        strokeLinecap="round"
      />
      {variant === "running" && (
        <circle cx="24" cy="6" r="3" fill="var(--primary)" />
      )}
    </svg>
  );
}

function LegendRow({
  sample,
  name,
  desc,
}: {
  sample: ReactNode;
  name: string;
  desc: string;
}) {
  return (
    <div className="flex items-center gap-3">
      <div className="flex w-14 shrink-0 items-center justify-center">
        {sample}
      </div>
      <div className="min-w-0 flex-1">
        <p className="text-sm text-foreground">{name}</p>
        <p className="text-xs leading-snug text-muted-foreground">{desc}</p>
      </div>
    </div>
  );
}

function LegendGroup({
  title,
  children,
}: { title: string; children: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <p className="mb-3 text-xs font-medium text-muted-foreground">{title}</p>
      <div className="space-y-3">{children}</div>
    </div>
  );
}

/**
 * 图例：节点 / 状态 / 连线 / 徽章。
 * 样式对齐协作图真实节点（AgentNode / StepEdge / EndpointNode）。
 */
export function GraphLegend() {
  return (
    <div className="grid gap-3 lg:grid-cols-2">
      <LegendGroup title="节点">
        <LegendRow
          sample={
            <IconChip>
              <UserRound size={16} className="text-muted-foreground" />
            </IconChip>
          }
          name="你的任务"
          desc="本回合你说的那句话；点它跳回完整提问。"
        />
        <LegendRow
          sample={
            <IconChip>
              <Bot size={16} className="text-muted-foreground" />
            </IconChip>
          }
          name="队员节点"
          desc="一位队员：角色 → 在干什么 → 用时 / 工具。"
        />
        <LegendRow
          sample={
            <IconChip>
              <Sparkles size={15} className="text-muted-foreground" />
            </IconChip>
          }
          name="CEO 汇总"
          desc="汇聚点：全队状态汇总到这里，最终答案也从这里进气泡。"
        />
      </LegendGroup>

      <LegendGroup title="状态（节点色环）">
        <LegendRow
          sample={<Bot size={18} className="text-muted-foreground" />}
          name="排队中"
          desc="还没轮到或在排队，灰环。"
        />
        <LegendRow
          sample={<Loader2 size={18} className="animate-spin text-primary" />}
          name="执行中"
          desc="正在干，蓝环 + 脉冲，入边走粒子。"
        />
        <LegendRow
          sample={<CheckCircle2 size={18} className="text-success" />}
          name="已完成"
          desc="绿环，完成时闪一下。"
        />
        <LegendRow
          sample={<XCircle size={18} className="text-destructive" />}
          name="失败"
          desc="红环；一个人挂了不必拖垮整队。"
        />
      </LegendGroup>

      <LegendGroup title="连线">
        <LegendRow
          sample={<EdgeSample variant="dep" />}
          name="依赖（实线）"
          desc="先后关系：无依赖可同时干，有依赖就等上游。"
        />
        <LegendRow
          sample={<EdgeSample variant="delegate" />}
          name="委派（虚线）"
          desc="队员再带小队时的委派线。"
        />
        <LegendRow
          sample={<EdgeSample variant="continuation" />}
          name="接续（点线）"
          desc="同一人带现场接着干（「续 ×N」），不是新队员。"
        />
        <LegendRow
          sample={<EdgeSample variant="running" />}
          name="运行中（粒子流）"
          desc="上游产出正往正在干活的节点流。"
        />
      </LegendGroup>

      <LegendGroup title="徽章">
        <LegendRow
          sample={
            <span className={graphBadgeMuted}>
              <PencilLine size={10} className="shrink-0" />
              {"续 ×N"}
            </span>
          }
          name="带现场续派"
          desc="同人接续角标：铅笔 +「续 ×N」；卡片优先露出改点。"
        />
        <LegendRow
          sample={<span className={graphBadgeMutedPlain}>第 N 轮</span>}
          name="第 N 轮"
          desc="辩论续轮角标（与侧栏轮次轨一致），不是带现场续派。"
        />
        <LegendRow
          sample={
            <span className="flex items-center gap-1">
              <StanceGlyph glyph="正" colorVar="var(--debate-side-pro)" />
              <StanceGlyph glyph="反" colorVar="var(--debate-side-con)" />
            </span>
          }
          name="辩论立场"
          desc="蓝 / 红阵营色 + 头像字；默认不写「正方 / 反方」。图上分带对置。"
        />
        <LegendRow
          sample={<span className={graphBadgeMutedPlain}>子任务</span>}
          name="子任务"
          desc="嵌套小队里的子队员（虚线委派连过来）。"
        />
      </LegendGroup>
    </div>
  );
}
