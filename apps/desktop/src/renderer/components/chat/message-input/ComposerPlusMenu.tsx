import { Button, IconButton } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ChevronLeft, FolderPlus, MoreVertical, Plus } from "lucide-react";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

export type PlusDrillId =
  | "workspace"
  | "model"
  | "assembly"
  | "permission"
  | "tools";

type PlusPanel = "list" | PlusDrillId;

type PlusHost = {
  panel: PlusPanel;
  drill: (id: PlusDrillId) => void;
  back: () => void;
  close: () => void;
  setHoldOpen: (hold: boolean) => void;
};

export type PlusRow =
  | { mode: "popover" }
  | {
      mode: "row";
      drill: () => void;
      close: () => void;
      setHoldOpen: (hold: boolean) => void;
    }
  | {
      mode: "panel";
      back: () => void;
      close: () => void;
      setHoldOpen: (hold: boolean) => void;
    }
  | { mode: "hidden" };

const PlusHostContext = createContext<PlusHost | null>(null);
const ComposerPlusCloseContext = createContext<(() => void) | null>(null);

/** Close the open bar「更多」menu (no-op outside the menu). */
export function useComposerPlusClose(): (() => void) | null {
  return useContext(ComposerPlusCloseContext);
}

export function useComposerPlusHost(): PlusHost | null {
  return useContext(PlusHostContext);
}

/** Chip inside「更多」: row on the list, in-place panel when drilled, hidden otherwise. */
export function useComposerPlusRow(id: PlusDrillId): PlusRow {
  const host = useComposerPlusHost();
  return useMemo(() => {
    if (!host) return { mode: "popover" };
    if (host.panel === "list") {
      return {
        mode: "row",
        drill: () => host.drill(id),
        close: host.close,
        setHoldOpen: host.setHoldOpen,
      };
    }
    if (host.panel === id) {
      return {
        mode: "panel",
        back: host.back,
        close: host.close,
        setHoldOpen: host.setHoldOpen,
      };
    }
    return { mode: "hidden" };
  }, [host, id]);
}

/** Back row for a drilled「更多」panel — same chrome as workspace nested views. */
export function ComposerPlusBackHeader({
  title,
  onBack,
}: {
  title: string;
  onBack: () => void;
}) {
  return (
    <div className="border-b border-border">
      <Button
        variant="ghost"
        aria-label="返回"
        className="h-auto w-full justify-start gap-2 rounded-none px-4 py-1.5 text-left text-xs font-medium text-muted-foreground"
        icon={
          <span className="flex w-4 shrink-0 justify-center">
            <ChevronLeft size={14} />
          </span>
        }
        onClick={onBack}
      >
        <span className="min-w-0 truncate text-foreground">{title}</span>
      </Button>
    </div>
  );
}

/**
 * 有会话后底栏「更多」（三点图标）：第一行添加引用目录，下面是这场的世界。
 * 工作区 / 权限 / 能力在同一面板内展开（返回 + 列表），不再叠第二层 Popover。
 */
export function ComposerPlusMenu({
  children,
  disabled,
}: {
  children: ReactNode;
  disabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [panel, setPanel] = useState<PlusPanel>("list");
  const [holdOpen, setHoldOpen] = useState(false);

  const close = useCallback(() => {
    setOpen(false);
    setPanel("list");
    setHoldOpen(false);
  }, []);

  const drill = useCallback((id: PlusDrillId) => {
    setPanel(id);
  }, []);

  const back = useCallback(() => {
    setPanel("list");
    setHoldOpen(false);
  }, []);

  const host = useMemo(
    () => ({ panel, drill, back, close, setHoldOpen }),
    [panel, drill, back, close],
  );

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next && holdOpen) return;
        setOpen(next);
        if (!next) {
          setPanel("list");
          setHoldOpen(false);
        }
      }}
      modal={false}
    >
      <PopoverTrigger asChild>
        <IconButton
          size="md"
          disabled={disabled}
          aria-label="更多"
          title="更多"
          data-testid="composer-plus-trigger"
        >
          <MoreVertical size={16} aria-hidden />
        </IconButton>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        avoidCollisions={false}
        className={
          panel === "list"
            ? "w-max min-w-56 p-2"
            : "min-w-64 w-max max-w-80 p-0"
        }
        onCloseAutoFocus={(e) => e.preventDefault()}
        onInteractOutside={(e) => {
          if (holdOpen) e.preventDefault();
        }}
      >
        <PlusHostContext.Provider value={host}>
          <ComposerPlusCloseContext.Provider value={close}>
            <div
              className="flex w-max min-w-0 flex-col items-stretch gap-1"
              data-testid="composer-plus-menu"
              data-plus-panel={panel}
            >
              {children}
            </div>
          </ComposerPlusCloseContext.Provider>
        </PlusHostContext.Provider>
      </PopoverContent>
    </Popover>
  );
}

/** 「更多」第二行：添加本机文件夹为这场对话的干活根（不改 folder_id）。 */
export function ComposerAddFolderRow({
  onAddFolder,
  disabled,
}: {
  onAddFolder: () => void;
  disabled?: boolean;
}) {
  const host = useComposerPlusHost();
  const close = useComposerPlusClose();
  if (host && host.panel !== "list") return null;
  return (
    <button
      type="button"
      data-testid="composer-add-folder"
      disabled={disabled}
      className="inline-flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-sm text-foreground hover:bg-accent/60 disabled:opacity-50"
      onClick={() => {
        close?.();
        onAddFolder();
      }}
    >
      <FolderPlus size={14} className="shrink-0" aria-hidden />
      添加文件夹
    </button>
  );
}

/** 「更多」第一行：关掉菜单，打开引用目录。钻进子面板时不画。 */
export function ComposerMoreAddRow({ onBrowse }: { onBrowse: () => void }) {
  const host = useComposerPlusHost();
  const close = useComposerPlusClose();
  if (host && host.panel !== "list") return null;
  return (
    <>
      <button
        type="button"
        data-testid="composer-more-add"
        className="inline-flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-sm text-foreground hover:bg-accent/60"
        onClick={() => {
          close?.();
          onBrowse();
        }}
      >
        <Plus size={14} className="shrink-0" aria-hidden />
        添加文件、文件夹或引用
      </button>
      <div className="my-0.5 h-px bg-border" />
    </>
  );
}
