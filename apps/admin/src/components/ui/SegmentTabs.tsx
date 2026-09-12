import { cn } from "@/lib/utils";
import { Link } from "react-router-dom";

export function SegmentTabs({
  items,
}: {
  items: readonly { to: string; label: string; active: boolean }[];
}) {
  return (
    <div className="inline-flex items-center rounded-lg border border-border p-0.5">
      {items.map((it) => (
        <Link
          key={it.to}
          to={it.to}
          aria-current={it.active ? "true" : undefined}
          className={cn(
            "inline-flex h-7 items-center rounded-lg px-3 text-sm font-medium outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
            it.active
              ? "bg-accent text-accent-foreground"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {it.label}
        </Link>
      ))}
    </div>
  );
}
