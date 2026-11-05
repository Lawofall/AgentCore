import { Button, Input } from "@/components/ui";
import { useCreateFolder } from "@/hooks/useFolders";
import { notifyError } from "@/lib/toast";
import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { Loader2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

/**
 * Composer「新建或加入」drill-in: name the new cloud folder before chatting.
 * Files-page create is inline (untitled then rename), not this form.
 */
export function CreateFolderCascadePanel({
  onClose,
  hideTitle = false,
}: {
  onClose: () => void;
  hideTitle?: boolean;
}) {
  const createFolder = useCreateFolder();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const pending = busy || createFolder.isPending;

  useEffect(() => {
    nameRef.current?.focus();
  }, []);

  const submit = async () => {
    const trimmed = name.trim();
    if (!trimmed || pending) return;
    setBusy(true);
    try {
      const { folder } = await createFolder.mutateAsync({
        name: trimmed,
        mode: "cloud",
      });
      const draft =
        useConversationStore.getState().currentConversationId === null;
      if (draft) {
        useFoldersStore.getState().setDraftWorkspaceIntent({
          kind: "folder",
          folderId: folder.id,
        });
      }
      useFoldersStore.getState().revealCreatedFolder(folder.id);
      onClose();
    } catch (e) {
      notifyError(e, "创建文件夹失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="w-full p-3">
      {hideTitle ? null : (
        <div className="mb-2 text-xs font-medium text-foreground">
          新建文件夹
        </div>
      )}
      <div className="space-y-2">
        <Input
          ref={nameRef}
          id="create-folder-name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === "Enter" && name.trim()) {
              e.preventDefault();
              void submit();
            }
          }}
          placeholder="文件夹名称"
          aria-label="文件夹名称"
          disabled={pending}
        />
        <p className="truncate text-xs text-muted-foreground">
          我的文件 · 云端
        </p>
        <div className="flex justify-end">
          <Button
            variant="primary"
            size="sm"
            disabled={!name.trim() || pending}
            onClick={() => void submit()}
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
    </div>
  );
}
