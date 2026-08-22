import { Button } from "@/components/ui";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import { useCreateFolder } from "@/hooks/useFolders";
import { notifyError } from "@/lib/toast";
import type { FolderMeta } from "@/services/folders";
import { useConversationStore } from "@/stores/conversation";
import { type CreateFolderAnchorRect, useFoldersStore } from "@/stores/folders";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * 「新建文件夹」锚点级联：在「我的文件」里建一个云端文件夹（顶层，或 `createFolderParent`
 * 指定的那一层里）。入口（chip / 文件页 + / 命令面板）共用；AppShell 挂载
 * {@link CreateFolderMenuHost}。打开本机文件夹是另一条路（§5.4：建容器只剩这两个动作）。
 */
export function CreateFolderMenuHost() {
  const open = useFoldersStore((s) => s.createFolderOpen);
  const anchor = useFoldersStore((s) => s.createFolderAnchor);
  const parent = useFoldersStore((s) => s.createFolderParent);
  const close = useFoldersStore((s) => s.closeCreateFolder);
  /** Swallow the outside-dismiss from the menu/dropdown that just opened us. */
  const ignoreOutsideUntil = useRef(0);

  useEffect(() => {
    if (open) ignoreOutsideUntil.current = Date.now() + 200;
  }, [open]);

  const guardOutside = (e: Event) => {
    if (Date.now() < ignoreOutsideUntil.current) e.preventDefault();
  };

  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <PopoverAnchor asChild>
        <VirtualAnchor rect={anchor} />
      </PopoverAnchor>
      <PopoverContent
        align={anchor ? "start" : "center"}
        side="bottom"
        sideOffset={anchor ? 6 : 0}
        avoidCollisions={false}
        className="w-64 p-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onPointerDownOutside={guardOutside}
        onInteractOutside={guardOutside}
      >
        {open ? (
          <CreateFolderCascadePanel
            onClose={close}
            parentId={parent?.id ?? null}
            parentName={parent?.name ?? null}
          />
        ) : null}
      </PopoverContent>
    </Popover>
  );
}

function VirtualAnchor({ rect }: { rect: CreateFolderAnchorRect | null }) {
  if (rect) {
    return (
      <div
        aria-hidden
        className="pointer-events-none fixed z-50"
        style={{
          top: rect.top,
          left: rect.left,
          width: Math.max(rect.width, 1),
          height: Math.max(rect.height, 1),
        }}
      />
    );
  }
  return (
    <div
      aria-hidden
      className="pointer-events-none fixed left-1/2 top-[18%] z-50 h-0 w-0 -translate-x-1/2"
    />
  );
}

export function CreateFolderCascadePanel({
  onClose,
  parentId = null,
  parentName = null,
  hideTitle = false,
}: {
  onClose: () => void;
  /** Nest inside this folder; null = 我的文件 top level. */
  parentId?: string | null;
  parentName?: string | null;
  /** Chip drill-in already shows the title in NestedHeader. */
  hideTitle?: boolean;
}) {
  const createFolder = useCreateFolder();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const applyDraftProjectIntent = (folderId: string) => {
    const draft =
      useConversationStore.getState().currentConversationId === null;
    if (draft) {
      useFoldersStore.getState().setDraftWorkspaceIntent({
        kind: "folder",
        folderId,
      });
    }
  };

  const finishCreated = (folder: FolderMeta) => {
    applyDraftProjectIntent(folder.id);
    useFoldersStore.getState().setPendingRename(folder.id);
    onClose();
  };

  const handleSubmitCloud = async () => {
    const trimmed = name.trim();
    if (!trimmed || busy || createFolder.isPending) return;
    setBusy(true);
    try {
      const { folder } = await createFolder.mutateAsync({
        name: trimmed,
        mode: "cloud",
        parentId,
      });
      finishCreated(folder);
    } catch (e) {
      notifyError(e, "创建文件夹失败");
    } finally {
      setBusy(false);
    }
  };

  const pending = busy || createFolder.isPending;

  return (
    <div className="w-full p-3">
      {hideTitle ? null : (
        <div className="mb-2 text-xs font-medium text-foreground">
          {parentName ? `在「${parentName}」里新建文件夹` : "新建文件夹"}
        </div>
      )}
      <NamePane
        inputRef={nameRef}
        name={name}
        setName={setName}
        pending={pending}
        hint={parentName ? `我的文件 · ${parentName}` : "我的文件 · 云端"}
        onSubmit={() => void handleSubmitCloud()}
      />
    </div>
  );
}

function NamePane({
  inputRef,
  name,
  setName,
  pending,
  hint,
  onSubmit,
}: {
  inputRef: React.RefObject<HTMLInputElement | null>;
  name: string;
  setName: (v: string) => void;
  pending: boolean;
  hint: string;
  onSubmit: () => void;
}) {
  return (
    <div className="space-y-2">
      <input
        ref={inputRef}
        value={name}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          e.stopPropagation();
          if (e.key === "Enter" && name.trim()) {
            e.preventDefault();
            onSubmit();
          }
        }}
        placeholder="文件夹名称"
        aria-label="文件夹名称"
        disabled={pending}
        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-50"
      />
      <p className="truncate text-xs text-muted-foreground">{hint}</p>
      <div className="flex justify-end">
        <Button
          variant="primary"
          size="sm"
          disabled={!name.trim() || pending}
          onClick={onSubmit}
        >
          {pending ? (
            <>
              <Loader2 size={14} className="animate-spin" />
              创建中…
            </>
          ) : (
            "创建"
          )}
        </Button>
      </div>
    </div>
  );
}
