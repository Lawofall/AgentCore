import {
  contextBlockSummaryLine,
  isCopyContextChannel,
} from "@/components/chat/contextBlockPresentation";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import { Button } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { formatCompact } from "@/lib/format";
import { usePersistentDisclosure } from "@/stores/disclosure";
import type { ContextBlockWire } from "@/types/events";
import { ChevronDown, ChevronRight, CornerDownRight } from "lucide-react";

/** Context channel → 中文 label + one-line hint (上下文传递可视化). The single source both
 * the run detail (worker 侧) and the CEO bubble (captain 侧) use to title each「收到的上下文」
 * block by its origin, so the user reads WHERE each piece came from, not just its raw
 * heading. `system`/`history` are the CEO-side opening channels (方案3 通道①). */
const CONTEXT_CHANNEL_META: Record<string, { label: string; hint: string }> = {
  system: { label: "系统提示", hint: "本回合 CEO 实际遵循的系统指令" },
  history: { label: "对话历史", hint: "本回合之前的往来" },
  request: { label: "原始请求", hint: "老板交给整个团队的目标" },
  team_position: { label: "团队位置", hint: "队友与产出去向" },
  dependency: { label: "前置结果", hint: "上游队友交付的产物" },
  workspace: { label: "工作区", hint: "共享工作区可读文件" },
  task: { label: "你的任务", hint: "分派给本 Agent 的具体活" },
  deliverable: { label: "交付物规格", hint: "本节点落点与结构约束" },
  team_brief: { label: "团队共识", hint: "本回合主协调为全员设定的共识" },
  gate_notes: {
    label: "把关要点",
    hint: "用户已放行的主 Agent 注意事项（非否决）",
  },
  steer: { label: "中途指示", hint: "执行中追加的操舵" },
  team_result: { label: "队员回传", hint: "委派的队员交回 CEO 的产物" },
  // 辩论续写通道 (continue_run 逐轮): what a 第 N 轮 debater was fed this round.
  round_focus: { label: "本轮焦点", hint: "这一轮辩论聚焦的争议点" },
  opponent: { label: "对方论点", hint: "对方上一轮的发言（供针对性回应）" },
  challenge: { label: "被驳命门", hint: "上一轮裁判记录你被反驳的点" },
  interjection: { label: "用户追问", hint: "用户本轮要求正面回应的问题" },
  cross_exam: { label: "质询", hint: "本轮定向质询：你被追问的问题" },
  closing: { label: "结辩", hint: "收场结辩：归纳本方胜局、不添新论据" },
  // 同人接续 (continue_run / 续派): the instruction this continuation was fed.
  continuation: {
    label: "接续指令",
    hint: "带着现场接着干的新指令（改稿或新任务）",
  },
};

/** Dependency fidelity → 中文 label (递指针/摘要/全文): HOW an upstream teammate's product
 * was handed to this run. */
const FIDELITY_META: Record<string, string> = {
  pointer: "递指针",
  summarize: "摘要",
  pass_through: "全文",
};

/**
 * 收到的上下文 (上下文传递可视化) — the structured context a run was actually fed at
 * assembly time (its `run_context` blocks), so the user sees exactly what the LLM read.
 * Worker 侧 (run detail): 原始请求 / 团队位置 / 上游产物 / 工作区 / 任务…; CEO 侧 (chat bubble):
 * 系统提示 / 对话历史 / 原始请求. Collapsible; worker 侧默认折叠（摘要可扫、全文按需展开）。
 * Each block shows its channel origin; a dependency block also surfaces its provenance
 * (来源 / 保真度 / 是否截断). The `system` block (verbatim CEO system prompt) is included.
 */
export function ReceivedContextSection({
  blocks,
  defaultExpanded,
  onNavigate,
  keyBase,
}: {
  blocks: ContextBlockWire[];
  defaultExpanded: boolean;
  /** 溯源可点击 (图↔上下文闭环): drill into the run a block came FROM — a dependency's upstream
   * author or a debate opponent's node. Given `source_run_id`; omit (CEO bubble) to keep
   * provenance read-only. The caller (run detail) guards that the target exists on this graph. */
  onNavigate?: (runId: string) => void;
  /** 回合/运行作用域标识：给了才把「收到的上下文」段 + 各块开合持久化（切对话/刷新后仍在）；
   *  缺省（如 on-demand 对话框）退化为会话内存态。 */
  keyBase?: string;
}) {
  const [expanded, setExpanded] = usePersistentDisclosure(
    keyBase ? `${keyBase}:ctx` : null,
    defaultExpanded,
  );
  const visible = blocks;
  if (visible.length === 0) return null;
  return (
    <section className="mb-4 last:mb-0">
      <Button
        variant="ghost"
        onClick={() => setExpanded((v) => !v)}
        className="h-auto w-full justify-start gap-1.5 px-0 py-0 hover:bg-transparent"
      >
        <span className="flex w-full items-center gap-1.5">
          {expanded ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )}
          <span className="flex-1 text-left text-xs font-medium text-muted-foreground">
            收到的上下文
          </span>
          <span className="shrink-0 text-xs tabular-nums text-muted-foreground">
            {visible.length} 段
          </span>
        </span>
      </Button>

      {expanded && (
        <div className="mt-2 space-y-1.5">
          {visible.map((b, i) => (
            <ContextBlockCard
              key={`${b.channel}-${i}`}
              block={b}
              defaultOpen={false}
              onNavigate={onNavigate}
              sceneKey={
                keyBase ? `${keyBase}:ctxblk:${b.channel}-${i}` : undefined
              }
            />
          ))}
        </div>
      )}
    </section>
  );
}

/**
 * 收到的上下文 · CEO 气泡入口 (上下文传递可视化) — the on-demand dialog the chat bubble's
 * hover action row opens to reveal the structured context the turn was actually fed. Unlike
 * the worker-side {@link ReceivedContextSection} (inline in the run detail panel), the CEO
 * bubble keeps this OFF the conversation flow: a turn no longer auto-expands a context block
 * on send; the user clicks「收到的上下文」to inspect it on demand.
 *
 * 决策② retired here: the verbatim 系统提示 (channel `system`) block is shown to EVERYONE in
 * this dialog (no 用量明细 gating). Being on-demand removes the「信息过载」concern, and the
 * prompt was already user-openable — so this also folds the old standalone「提示词」button in
 * (its content == the `system` block). Renders nothing when the turn carried no context.
 */
/** Controlled dialog — trigger lives in {@link AssistantMessageFooter}「更多」菜单。 */
export function ReceivedContextDialog({
  blocks,
  open,
  onOpenChange,
}: {
  blocks: ContextBlockWire[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  if (blocks.length === 0) return null;
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[80vh] max-w-2xl flex-col">
        <DialogHeader>
          <DialogTitle>收到的上下文</DialogTitle>
          <DialogDescription>
            本回合 AI 实际读到的上下文，与喂给模型的逐字一致（系统提示 /
            对话历史 / 原始请求 …）。
          </DialogDescription>
        </DialogHeader>
        <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-5 pb-5">
          {blocks.map((b, i) => (
            <ContextBlockCard
              key={`${b.channel}-${i}`}
              block={b}
              defaultOpen={false}
            />
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** One「收到的上下文」block. Copy-type channels default to a citation card (collapsed
 * summary + expand for verbatim body); incremental channels keep the original segment card.
 * Pass `presentation="incremental"` to force the segment card. */
export function ContextBlockCard({
  block,
  defaultOpen,
  onNavigate,
  sceneKey,
  presentation = "auto",
}: {
  block: ContextBlockWire;
  defaultOpen: boolean;
  onNavigate?: (runId: string) => void;
  /** 持久化作用域键（`${keyBase}:ctxblk:${channel}-${i}`）；缺省退化为会话内存态。 */
  sceneKey?: string;
  /**
   * `auto` — copy channels → citation card; others → incremental segment card.
   * `incremental` — always the segment card.
   */
  presentation?: "auto" | "incremental";
}) {
  const asCitation =
    presentation === "auto" && isCopyContextChannel(block.channel);
  if (asCitation) {
    return (
      <CitationContextCard
        block={block}
        defaultOpen={defaultOpen}
        onNavigate={onNavigate}
        sceneKey={sceneKey}
      />
    );
  }
  return (
    <IncrementalContextCard
      block={block}
      defaultOpen={defaultOpen}
      onNavigate={onNavigate}
      sceneKey={sceneKey}
    />
  );
}

/** Copy-type channel: collapsed citation row; expand reveals the verbatim body the LLM read. */
function CitationContextCard({
  block,
  defaultOpen,
  onNavigate,
  sceneKey,
}: {
  block: ContextBlockWire;
  defaultOpen: boolean;
  onNavigate?: (runId: string) => void;
  sceneKey?: string;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    sceneKey ?? null,
    defaultOpen,
  );
  const meta = CONTEXT_CHANNEL_META[block.channel] ?? {
    label: block.channel,
    hint: "",
  };
  const summary =
    contextBlockSummaryLine(block.body) || block.heading || meta.hint;
  const canJump = Boolean(onNavigate && block.source_run_id);

  return (
    <div className="rounded-lg border border-border/50 bg-muted/40 px-2.5 py-1.5 text-xs">
      <div className="flex items-start gap-2">
        <Button
          variant="ghost"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="mt-0.5 h-auto shrink-0 px-0 py-0 hover:bg-transparent"
        >
          {open ? (
            <ChevronDown size={12} className="text-muted-foreground" />
          ) : (
            <ChevronRight size={12} className="text-muted-foreground" />
          )}
        </Button>
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary"
            >
              {meta.label}
            </button>
            {block.source_role ? (
              canJump ? (
                <Button
                  variant="ghost"
                  onClick={() => onNavigate?.(block.source_run_id)}
                  title="跳到来源节点"
                  className="h-auto gap-1 rounded bg-background px-1.5 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
                >
                  <span>来自 {block.source_role}</span>
                  <CornerDownRight size={11} className="shrink-0" />
                </Button>
              ) : (
                <span className="rounded bg-background px-1.5 py-0.5 text-muted-foreground">
                  来自 {block.source_role}
                </span>
              )
            ) : null}
            {block.fidelity ? (
              <span className="rounded bg-background px-1.5 py-0.5 text-muted-foreground">
                {FIDELITY_META[block.fidelity] ?? block.fidelity}
              </span>
            ) : null}
            {block.truncated ? (
              <span className="rounded bg-background px-1.5 py-0.5 text-muted-foreground">
                已截断
              </span>
            ) : null}
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="ml-auto shrink-0 tabular-nums text-muted-foreground/60"
            >
              {formatCompact(block.chars)} 字
            </button>
          </div>
          {!open && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="block w-full truncate text-left text-muted-foreground"
            >
              {summary}
            </button>
          )}
        </div>
      </div>

      {open && (
        <div className="mt-1.5 space-y-1.5 pl-[18px]">
          <PromptDocument text={block.body} maxHeightClass="max-h-72" />
          {block.files.length > 0 && (
            <div className="space-y-0.5">
              {block.files.map((f) => (
                <div
                  key={f}
                  className="flex items-center gap-1.5 text-muted-foreground"
                >
                  <CornerDownRight size={11} className="shrink-0" />
                  <span className="truncate font-mono">{f}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Incremental / debate-structure / diagnostic segment card — prior default presentation. */
function IncrementalContextCard({
  block,
  defaultOpen,
  onNavigate,
  sceneKey,
}: {
  block: ContextBlockWire;
  defaultOpen: boolean;
  onNavigate?: (runId: string) => void;
  sceneKey?: string;
}) {
  const [open, setOpen] = usePersistentDisclosure(
    sceneKey ?? null,
    defaultOpen,
  );
  const meta = CONTEXT_CHANNEL_META[block.channel] ?? {
    label: block.channel,
    hint: "",
  };
  // Provenance line (来自 {role} · 保真度 · 截断) for blocks that carry an origin: a worker's
  // upstream dependency (通道②), the CEO's team readback (通道⑤ team_result), and a debate
  // 续写轮's opponent block (carries the opposing side's role + clip fidelity).
  const hasProvenance =
    block.channel === "dependency" ||
    block.channel === "team_result" ||
    block.channel === "opponent";
  const peek = block.body.slice(0, 140);
  return (
    <div className="rounded-lg bg-muted px-2.5 py-1.5 text-xs">
      <Button
        variant="ghost"
        onClick={() => setOpen((v) => !v)}
        className="h-auto w-full justify-start gap-2 px-0 py-0 hover:bg-transparent"
      >
        <span className="flex w-full items-center gap-2 text-left">
          {open ? (
            <ChevronDown
              size={12}
              className="mt-0.5 shrink-0 self-start text-muted-foreground"
            />
          ) : (
            <ChevronRight
              size={12}
              className="mt-0.5 shrink-0 self-start text-muted-foreground"
            />
          )}
          <span className="min-w-0 flex-1">
            <span className="flex items-center gap-1.5">
              <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 font-medium text-primary">
                {meta.label}
              </span>
              <span className="min-w-0 flex-1 truncate text-foreground">
                {block.heading}
              </span>
            </span>
            {!open && (
              <span className="mt-0.5 block truncate text-muted-foreground/70">
                {peek || meta.hint}
              </span>
            )}
          </span>
          <span className="shrink-0 tabular-nums text-muted-foreground/60">
            {formatCompact(block.chars)} 字
          </span>
        </span>
      </Button>

      {open && (
        <div className="mt-1.5 space-y-1.5 pl-[18px]">
          {hasProvenance &&
            (block.source_role || block.fidelity || block.truncated) && (
              <div className="flex flex-wrap items-center gap-1.5 text-muted-foreground/80">
                {block.source_role &&
                  (onNavigate && block.source_run_id ? (
                    <Button
                      variant="ghost"
                      onClick={() => onNavigate(block.source_run_id)}
                      title="跳到来源节点"
                      className="h-auto gap-1 rounded bg-background px-1.5 py-0.5 text-muted-foreground/80 hover:bg-accent hover:text-foreground"
                    >
                      <span>来自 {block.source_role}</span>
                      <CornerDownRight size={11} className="shrink-0" />
                    </Button>
                  ) : (
                    <span className="rounded bg-background px-1.5 py-0.5">
                      来自 {block.source_role}
                    </span>
                  ))}
                {block.fidelity && (
                  <span className="rounded bg-background px-1.5 py-0.5">
                    {FIDELITY_META[block.fidelity] ?? block.fidelity}
                  </span>
                )}
                {block.truncated && (
                  <span className="rounded-lg bg-background px-1.5 py-0.5 text-muted-foreground">
                    已截断
                  </span>
                )}
              </div>
            )}
          <PromptDocument text={block.body} maxHeightClass="max-h-72" />
          {block.files.length > 0 && (
            <div className="space-y-0.5">
              {block.files.map((f) => (
                <div
                  key={f}
                  className="flex items-center gap-1.5 text-muted-foreground"
                >
                  <CornerDownRight size={11} className="shrink-0" />
                  <span className="truncate font-mono">{f}</span>
                </div>
              ))}
            </div>
          )}
          {block.truncated && !hasProvenance && (
            <p className="text-muted-foreground/60">
              （仅展示节选，完整 {formatCompact(block.chars)} 字已传给 AI）
            </p>
          )}
        </div>
      )}
    </div>
  );
}
