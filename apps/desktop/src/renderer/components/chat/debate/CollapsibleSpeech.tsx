import { usePersistentDisclosure } from "@/stores/disclosure";
import { type ReactNode, useLayoutEffect, useRef, useState } from "react";

/** 用户气泡 / 插话的折叠阈值（px）：约 6–8 行（偏 ChatGPT 紧）。 */
export const USER_BUBBLE_COLLAPSED_MAX_H = 144;

/** 发言气泡默认折叠阈值（px，旧 `max-h-72` 等价值）。 */
const DEFAULT_COLLAPSED_MAX_H = 288;

/** 判定溢出的余量（px）：躲开亚像素行高抖动，别为半行高度长出一枚按钮。 */
const OVERFLOW_SLACK = 4;

/**
 * 长发言折叠壳（辩论室发言气泡 / 擂台发言格共用）—— 取代旧的 `max-h-* overflow-y-auto` 内嵌滚动
 * 条：短/中发言原样全展（绝大多数，零跳变），只有**真的超长**才夹到 288px + 底部渐隐 + 一枚
 * 「展开全文 / 收起」。这既去掉画布放大态里的**嵌套滚动条 / 滚动陷阱**，也消除旧版「流式不夹、收场
 * 才夹」的高度跳变（收场后只有超长才收，且是显式渐隐而非突兀滚动框）。
 *
 * `fadeToClass` 是折叠渐隐要融进的底色（发言气泡 `from-card`），随宿主气泡底色传入，让渐隐无缝。
 * 纯渲染、无副作用（仅测量内容高度）。
 */
export function CollapsibleSpeech({
  contentKey,
  fadeToClass = "from-card",
  collapsedMaxH = DEFAULT_COLLAPSED_MAX_H,
  sceneKey,
  children,
}: {
  /** 内容指纹（发言全文串）：变化时重测是否溢出，避免流式收场后残留旧判定。 */
  contentKey: string;
  /** 折叠渐隐融入的宿主底色 Tailwind `from-*` 类（默认发言气泡 `from-card`）。 */
  fadeToClass?: string;
  /** 折叠态的最大高度（px）；主对话长回答用更高的阈值，只夹真正超长的答案，
   * 短/中答案原样全展。 */
  collapsedMaxH?: number;
  /** 持久化作用域键（回合+轮+方标识）：给了才把「展开全文」跨卸载/刷新记住；缺省走会话内存态。 */
  sceneKey?: string;
  children: ReactNode;
}) {
  const [expanded, setExpanded] = usePersistentDisclosure(
    sceneKey ?? null,
    false,
  );
  const [overflow, setOverflow] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // 量「未夹层的内容高 vs 阈值」，而不是量当前渲染出来的高度差：展开态下容器本就没有夹层、
  // scrollHeight 恒等于 clientHeight，按渲染高判定必然得出「不溢出」——于是被 sceneKey 记住
  // 展开态的气泡一旦重挂载（切对话回来 / 编辑取消 / 重启），「收起」按钮就再也
  // 长不出来，长文永久全展。判定与展开态解耦后，两态下测得同一个结论。
  // biome-ignore lint/correctness/useExhaustiveDependencies: contentKey is an intentional re-run key — re-measure overflow when the speech content changes.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const measure = () =>
      setOverflow(el.scrollHeight > collapsedMaxH + OVERFLOW_SLACK);
    measure();
    // 宽度变化（侧栏折叠 / 窗口缩放 / 画布分栏）会重排文本、异步内容（图片、公式）会后长高，
    // 一次性判定会残留：宽变窄时内容被夹住却没有「展开全文」。故挂载后持续跟测。
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [contentKey, collapsedMaxH]);

  return (
    <div>
      <div
        className={expanded ? "text-sm" : "relative overflow-hidden text-sm"}
        style={expanded ? undefined : { maxHeight: collapsedMaxH }}
      >
        {/* 测量层：不带夹层，故 scrollHeight 恒为完整内容高，与展开态无关。 */}
        <div ref={ref}>{children}</div>
        {!expanded && overflow && (
          <div
            className={`pointer-events-none absolute inset-x-0 bottom-0 h-8 bg-gradient-to-t to-transparent ${fadeToClass}`}
            aria-hidden
          />
        )}
      </div>
      {overflow && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-1 text-xs font-medium text-primary hover:underline"
        >
          {expanded ? "收起" : "展开全文"}
        </button>
      )}
    </div>
  );
}
