/**
 * 图头「待你拍板 N」行动条（批 R3 决策 3 · 方案 C 收敛）。
 *
 * 聚合本回合全部待拍板（{@link GraphPendingDecision}），一处可达：点「待你拍板 N」
 * 展开清单，逐条点击定位到对应节点（折叠幕内先聚焦该幕）。**只导航、不建卡**——
 * 真正的拍板卡仍在聊天流既有面（不与其重复）。**仅同时 ≥2 项待决
 * 才渲染**（方案 C「一个焦点 + 一个入口」：单项由 ResumePrompt / 决策区独占表达，
 * 胶囊只在需要「逐条定位」时出现；无待拍板同样不渲染）。挂各宿主既有图头
 * chrome：绝对定位在图区左上。
 */

import { cn } from "@/lib/utils";
import { ChevronDown, Gavel, Pause, Wrench } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import type {
  GraphPendingDecision,
  GraphPendingKind,
} from "./pendingDecisions";

function KindIcon({ kind }: { kind: GraphPendingKind }) {
  if (kind === "checkpoint") return <Pause size={13} className="shrink-0" />;
  if (kind === "approval") return <Wrench size={13} className="shrink-0" />;
  return <Gavel size={13} className="shrink-0" />;
}

export function GraphActionBar({
  decisions,
  onLocate,
  className,
}: {
  decisions: GraphPendingDecision[];
  onLocate: (decision: GraphPendingDecision) => void;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const listId = useId();
  const count = decisions.length;

  // 待拍板降到阈值以下（都拍完了 / 只剩单项）→ 收起，避免悬着空面板。
  useEffect(() => {
    if (count < 2) setOpen(false);
  }, [count]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        setOpen(false);
      }
    };
    const onPointer = (e: PointerEvent) => {
      if (e.target instanceof Node && rootRef.current?.contains(e.target))
        return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey, true);
    const id = window.setTimeout(
      () => window.addEventListener("pointerdown", onPointer),
      0,
    );
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.clearTimeout(id);
      window.removeEventListener("pointerdown", onPointer);
    };
  }, [open]);

  const locate = useCallback(
    (decision: GraphPendingDecision) => {
      setOpen(false);
      onLocate(decision);
    },
    [onLocate],
  );

  // 方案 C：单项待决不出胶囊（ResumePrompt / 决策区已是唯一焦点），≥2 才值得聚合定位。
  if (count < 2) return null;

  return (
    <div
      ref={rootRef}
      className={cn("absolute left-3 top-3 z-10", className)}
      onContextMenu={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={`待你拍板 ${count} 项，展开定位`}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 rounded-full border border-primary/40 bg-card/95 px-2.5 py-1 text-xs font-medium text-primary shadow-sm backdrop-blur transition-colors hover:bg-primary/10 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
      >
        <Gavel size={13} className="shrink-0" />
        待你拍板
        <span className="tabular-nums">{count}</span>
        <ChevronDown
          size={13}
          className={cn("shrink-0 transition-transform", open && "rotate-180")}
        />
      </button>

      {open && (
        <div
          id={listId}
          role="menu"
          aria-label="待你拍板清单"
          className="mt-1 w-64 overflow-hidden rounded-xl border border-border bg-card/98 shadow-lg backdrop-blur"
        >
          <ul className="max-h-72 overflow-y-auto py-1">
            {decisions.map((d) => (
              <li key={d.id}>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => locate(d)}
                  className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
                >
                  <span className="text-primary">
                    <KindIcon kind={d.kind} />
                  </span>
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                    {d.title}
                  </span>
                  <span className="shrink-0 text-muted-foreground">
                    {d.detail}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
