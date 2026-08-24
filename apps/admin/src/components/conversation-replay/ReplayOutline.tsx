import { cn } from "@/lib/utils";
import { ListTree, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";

export type ReplayOutlineTurn = {
  id: string;
  label: string;
};

/**
 * Desktop ConversationOutline analog: a floating TOC over user turns.
 * Replay has no store `focusMessage`, so the parent scrolls via `onJump`.
 */
export function ReplayOutline({
  turns,
  onJump,
}: {
  turns: ReplayOutlineTurn[];
  onJump: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  if (turns.length < 2) return null;

  const jump = (id: string) => {
    onJump(id);
    setOpen(false);
  };

  return (
    <div ref={rootRef} className="absolute bottom-3 right-3 z-20">
      {open && (
        <div className="absolute right-0 bottom-11 max-h-[60vh] w-72 overflow-y-auto rounded-lg border border-border bg-card p-1">
          <div className="flex items-center justify-between px-2 py-1.5">
            <span className="text-muted-foreground text-xs font-medium">
              对话大纲 · {turns.length} 个回合
            </span>
            <button
              type="button"
              aria-label="关闭大纲"
              onClick={() => setOpen(false)}
              className="rounded-md p-1 text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X size={13} />
            </button>
          </div>
          <ul className="space-y-0.5">
            {turns.map((t, i) => (
              <li key={t.id}>
                <button
                  type="button"
                  onClick={() => jump(t.id)}
                  className="flex w-full items-start gap-2 rounded-lg px-2 py-1.5 text-left text-foreground text-sm outline-none hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="mt-0.5 w-5 shrink-0 text-right text-muted-foreground text-xs tabular-nums">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate">
                    {t.label || "（空消息）"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button
        type="button"
        aria-label="对话大纲"
        aria-pressed={open}
        title="对话大纲 · 跳转回合"
        onClick={() => setOpen((v) => !v)}
        className={cn(
          "rounded-full border border-border bg-card p-2 text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring",
        )}
      >
        <ListTree size={16} />
      </button>
    </div>
  );
}
