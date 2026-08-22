import { Button, IconButton } from "@/components/ui";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { ChevronLeft, Plus } from "lucide-react";
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react";

export type PlusDrillId = "workspace" | "model" | "permission";

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

/** Close the open bar「＋」menu (no-op outside the menu). */
export function useComposerPlusClose(): (() => void) | null {
  return useContext(ComposerPlusCloseContext);
}

export function useComposerPlusHost(): PlusHost | null {
  return useContext(PlusHostContext);
}

/** Chip inside「＋」: row on the list, in-place panel when drilled, hidden otherwise. */
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

/** Back row for a drilled「＋」panel — same chrome as workspace nested views. */
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
 * 底栏 bar 的「＋」外壳：低频/绑定后少改的会话配置由调用方塞进菜单。
 * 工作区 / 模型 / 权限在同一面板内展开（返回 + 列表），不再叠第二层 Popover。
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
          aria-label="更多选项"
          aria-expanded={open}
          data-testid="composer-plus-trigger"
        >
          <Plus size={16} />
        </IconButton>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        avoidCollisions={false}
        className={
          panel === "list" ? "w-max p-2" : "min-w-64 w-max max-w-80 p-0"
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
