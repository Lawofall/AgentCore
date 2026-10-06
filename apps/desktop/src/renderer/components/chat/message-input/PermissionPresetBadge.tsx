import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { SimpleTooltip } from "@/components/ui/tooltip";
import {
  patchConversationCache,
  useConversations,
} from "@/hooks/useConversations";
import { hasLocalEngine } from "@/lib/capabilities";
import { useChatPaneId } from "@/lib/chatPane";
import { notifyError, notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  BOUNDARY_LABELS,
  BOUNDARY_ORDER,
  DEFAULT_PERMISSION_AXES,
  type PermissionAxes,
  axesEqual,
  boundaryShortLabel,
  confirmComputerIfNeeded,
  resolveDefaultPermissionAxes,
  setComposerDraftAxes,
  setConversationPermissionAxes,
  setUserDefaultRecipe,
} from "@/services/permissionAxes";
import { usePermissionChangeStore } from "@/stores/permissionChanges";
import { ChevronDown, Shield } from "lucide-react";
import { useEffect, useState } from "react";
import { ComposerPlusBackHeader, useComposerPlusRow } from "./ComposerPlusMenu";

/**
 * Composer boundary badge. Three choices; 这台电脑 only when a local engine
 * is present (or the conversation is already on that boundary).
 */
export function PermissionAxesBadge({
  disabled,
  iconOnly = false,
}: {
  disabled?: boolean;
  /** 二级入口：只显示盾牌图标，标签进 tooltip / aria-label。 */
  iconOnly?: boolean;
}) {
  const conversationId = useChatPaneId();
  const conversations = useConversations();
  const [draftAxes, setDraftAxes] = useState<PermissionAxes>(
    DEFAULT_PERMISSION_AXES,
  );
  const plus = useComposerPlusRow("permission");
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);

  const fromCache = conversationId
    ? conversations.find((c) => c.id === conversationId)?.permissionAxes
    : undefined;
  const axes = fromCache ?? draftAxes;
  const label = boundaryShortLabel(axes.boundary);
  const tip = BOUNDARY_LABELS[axes.boundary].description;

  useEffect(() => {
    if (fromCache) return;
    let alive = true;
    void resolveDefaultPermissionAxes().then((a) => {
      if (alive) setDraftAxes(a);
    });
    return () => {
      alive = false;
    };
  }, [fromCache]);

  const options = BOUNDARY_ORDER.filter(
    (id) =>
      id !== "computer" || hasLocalEngine() || axes.boundary === "computer",
  );

  const apply = async (next: PermissionAxes) => {
    if (pending || disabled) return;
    if (axesEqual(next, axes)) return;
    if (!confirmComputerIfNeeded(axes, next)) return;
    if (plus.mode === "panel" || plus.mode === "row") plus.close();
    else setOpen(false);
    if (!conversationId) {
      setDraftAxes(next);
      setComposerDraftAxes(next);
      return;
    }
    setPending(true);
    try {
      const saved = await setConversationPermissionAxes(conversationId, next);
      patchConversationCache(conversationId, { permissionAxes: saved });
      void usePermissionChangeStore
        .getState()
        .load(conversationId)
        .catch(() => {});
    } catch (e) {
      notifyError(e, "切换权限失败");
    } finally {
      setPending(false);
    }
  };

  const setAsSessionDefault = async () => {
    if (pending || disabled) return;
    setPending(true);
    try {
      const saved = await setUserDefaultRecipe(axes.boundary);
      notifySuccess(`新会话将默认「${BOUNDARY_LABELS[saved].short}」`);
    } catch (e) {
      notifyError(e, "设置默认失败");
    } finally {
      setPending(false);
    }
  };

  if (plus.mode === "hidden") return null;

  const tipWithLabel = iconOnly ? `${label} — ${tip}` : tip;

  const panel = (
    <>
      <p className="px-1 pb-1.5 text-xs font-medium text-muted-foreground">
        边界
      </p>
      <div className="space-y-0.5">
        {options.map((id) => {
          const selected = axes.boundary === id;
          const meta = BOUNDARY_LABELS[id];
          return (
            <SimpleTooltip key={id} label={meta.description}>
              <button
                type="button"
                aria-current={selected ? "true" : undefined}
                onClick={() => void apply({ boundary: id })}
                className={cn(
                  "flex w-full items-baseline gap-1.5 rounded-lg px-2.5 py-1.5 text-left",
                  selected ? "bg-primary/10" : "hover:bg-accent/50",
                )}
              >
                <span className="shrink-0 text-sm font-medium text-foreground">
                  {meta.short}
                  {id === "folder" ? " · 荐" : ""}
                </span>
                <span className="min-w-0 truncate text-xs text-muted-foreground">
                  {meta.description}
                </span>
              </button>
            </SimpleTooltip>
          );
        })}
      </div>

      <div className="mt-2 border-t border-border/60 px-1 pt-2">
        <SimpleTooltip label="写入账户默认；只影响之后新建的对话">
          <span className="block">
            <button
              type="button"
              disabled={pending || disabled}
              onClick={() => void setAsSessionDefault()}
              className={cn(
                "w-full rounded-lg px-2.5 py-1.5 text-left text-xs font-medium",
                pending || disabled
                  ? "cursor-not-allowed text-muted-foreground/50"
                  : "text-foreground hover:bg-accent/50",
              )}
            >
              设为新会话默认
            </button>
          </span>
        </SimpleTooltip>
      </div>
    </>
  );

  const trigger = (
    <button
      type="button"
      disabled={disabled || pending}
      aria-label={`权限：${label}`}
      aria-expanded={plus.mode === "panel" || open}
      onClick={plus.mode === "row" ? plus.drill : undefined}
      className={cn(
        "inline-flex items-center rounded-lg text-xs text-muted-foreground hover:bg-accent/60 hover:text-foreground",
        iconOnly ? "size-8 justify-center" : "h-8 max-w-44 gap-1 px-2",
        (disabled || pending) && "cursor-not-allowed opacity-60",
      )}
    >
      <Shield size={14} className="shrink-0" />
      {!iconOnly && (
        <>
          <span className="truncate">{label}</span>
          <ChevronDown size={12} className="shrink-0 opacity-60" />
        </>
      )}
    </button>
  );

  if (plus.mode === "panel") {
    return (
      <div className="w-80 p-0">
        <ComposerPlusBackHeader title="权限" onBack={plus.back} />
        <div className="p-2">{panel}</div>
      </div>
    );
  }

  if (plus.mode === "row") {
    return <SimpleTooltip label={tipWithLabel}>{trigger}</SimpleTooltip>;
  }

  return (
    <div className="relative shrink-0">
      <Popover open={open} onOpenChange={setOpen}>
        <SimpleTooltip label={tipWithLabel}>
          <PopoverTrigger asChild>{trigger}</PopoverTrigger>
        </SimpleTooltip>
        <PopoverContent
          side="bottom"
          align="start"
          avoidCollisions={false}
          onCloseAutoFocus={(e) => e.preventDefault()}
          className="w-80 p-2"
        >
          {panel}
        </PopoverContent>
      </Popover>
    </div>
  );
}
