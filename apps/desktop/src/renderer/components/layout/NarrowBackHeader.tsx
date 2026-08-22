import { IconButton } from "@/components/ui";
import { ArrowLeft } from "lucide-react";

/** 窄屏推进页顶栏：返回 + 标题。 */
export function NarrowBackHeader({
  title,
  onBack,
}: {
  title: string;
  onBack: () => void;
}) {
  return (
    <header className="flex h-12 shrink-0 items-center gap-1 border-b border-border bg-card px-2 pt-[env(safe-area-inset-top)]">
      <IconButton size="md" aria-label="返回" onClick={onBack}>
        <ArrowLeft size={18} />
      </IconButton>
      <h1 className="min-w-0 flex-1 truncate text-sm font-medium">{title}</h1>
    </header>
  );
}
