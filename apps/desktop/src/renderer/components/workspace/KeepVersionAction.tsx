import { Button, Input } from "@/components/ui";
import type { VersionSource } from "@/components/workspace/changesTimeline";
import { notifyError, notifySuccess } from "@/lib/toast";
import { createLocalVersion } from "@/services/localWorkspaceVersions";
import { wsCreateSnapshot } from "@/services/workspaces";
import { Bookmark, Loader2 } from "lucide-react";
import { useState } from "react";

/**
 * 「留版本」入口 —— 折叠成一个按钮，展开才占一行输入。
 * 挂在文件页「版本」面板（云端 / 本机同一按钮）；右坞「改动」tab 不再提供本动作。
 */
export function KeepVersionAction({
  source,
  onCreated,
  emphasis = false,
}: {
  source: VersionSource;
  onCreated: () => void;
  /** 空态：这是面板上唯一的行动点，用主按钮。 */
  emphasis?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [label, setLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const trimmed = label.trim();

  const close = () => {
    setOpen(false);
    setLabel("");
  };

  const submit = async () => {
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      if (source.origin === "localWs") {
        await createLocalVersion(
          { rootId: source.rootId, subpath: source.subpath },
          trimmed,
        );
      } else {
        await wsCreateSnapshot(source.wsId, trimmed);
      }
      notifySuccess(`已留版本「${trimmed}」`);
      close();
      onCreated();
    } catch (e) {
      notifyError(e, "留版本失败");
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <Button
        variant={emphasis ? "primary" : "neutral"}
        icon={<Bookmark size={13} />}
        aria-label="留版本"
        title="为当前工作区留一个命名版本，之后随时回到这里"
        onClick={() => setOpen(true)}
      >
        留版本
      </Button>
    );
  }

  return (
    <div className="flex w-full items-center gap-1.5">
      <Input
        autoFocus
        aria-label="版本名"
        placeholder="版本名"
        maxLength={200}
        value={label}
        disabled={saving}
        onChange={(e) => setLabel(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
          if (e.key === "Escape") close();
        }}
        className="h-7 min-w-0 flex-1 text-xs"
      />
      <Button
        disabled={!trimmed || saving}
        onClick={() => void submit()}
        icon={saving ? <Loader2 size={13} className="animate-spin" /> : null}
      >
        保存
      </Button>
      <Button variant="ghost" disabled={saving} onClick={close}>
        取消
      </Button>
    </div>
  );
}
