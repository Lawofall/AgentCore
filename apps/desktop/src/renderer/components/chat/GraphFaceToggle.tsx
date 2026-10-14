import { IconButton } from "@/components/ui";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useChatPaneSliceKey } from "@/lib/chatPane";
import { cn } from "@/lib/utils";
import { useActiveHasMessages } from "@/stores/conversation";
import { useGraphFaceStore } from "@/stores/graphFace";
import { Network } from "lucide-react";

/**
 * 正文 / 图切换。不进消息列的文档流：宽屏浮在侧面板按钮下面，
 * 窄屏进顶栏，并排两栏时进该栏标题行。点下去整列换成画布。
 */
export function GraphFaceToggle({
  chrome = "float",
  iconSize = 16,
}: {
  chrome?: "float" | "plain";
  iconSize?: number;
}) {
  const hasMessages = useActiveHasMessages();
  const key = useChatPaneSliceKey();
  const onGraph = useGraphFaceStore((s) => s.byKey[key] === "graph");
  const toggle = useGraphFaceStore((s) => s.toggle);
  if (!hasMessages) return null;
  const label = onGraph ? "正文" : "图";
  return (
    <SimpleTooltip label={label}>
      <IconButton
        size="md"
        aria-label={label}
        aria-pressed={onGraph}
        onClick={() => toggle(key)}
        className={cn(
          chrome === "float" && "border border-border bg-card/80 backdrop-blur",
          onGraph && "bg-accent text-foreground",
        )}
      >
        <Network size={iconSize} />
      </IconButton>
    </SimpleTooltip>
  );
}
