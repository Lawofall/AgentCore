/**
 * Disabled composer stand-in so the reading column's bottom rhythm matches
 * the desktop in-session bar. No send, no attachments, no stop.
 */
export function ReplayComposerGhost() {
  return (
    <div className="mx-auto w-full min-w-0 max-w-3xl px-6 pb-4 pt-1">
      <div
        aria-label="只读输入"
        className="flex min-h-11 items-center rounded-xl border border-border bg-muted/40 px-4 py-2.5 text-muted-foreground text-sm"
      >
        只读复盘
      </div>
    </div>
  );
}
