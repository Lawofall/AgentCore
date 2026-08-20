import { statusCardChrome } from "@/components/ui/tone-presets";
import { useFolders, useUpdateFolder } from "@/hooks/useFolders";
import { notifyError } from "@/lib/toast";
import { filesFocusState } from "@/pages/conversations/constants";
import type { AutoFolderNotice } from "@/stores/conversation";
import { ChevronRight, FolderPlus } from "lucide-react";
import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

/**
 * 裸聊写盘落点告知（双模式工作区 §5.4 裸聊行）的内容行。
 *
 * 没选文件夹就开聊、AI 又要写文件时，运行时按话题建了一个云文件夹。这条提示把落点
 * 说出来，并给出「打开」和当场改名——**告知不是审批**：回合不为它停，卡上没有必须点
 * 的按钮，忽略它也一切照旧。
 *
 * 名字以文件夹列表里的现名为准（`folderId` 查），事件里的名字只作兜底，这样用户改完名
 * 提示立刻跟着变、刷新后也不会退回旧名。
 *
 * 两个落点见 {@link AutoFolderNoticeLine}（有产出文件）与 {@link AutoFolderNoticeCard}
 * （没有产出文件）；两者都排在答复正文之后——建桌发生在派工前、文件还没写，挂气泡顶部
 * 会让用户先读到落点、再听 AI 说要干什么。
 */
function AutoFolderNoticeBody({
  notice,
  lead,
}: {
  notice: AutoFolderNotice;
  lead: string;
}) {
  const navigate = useNavigate();
  const chrome = statusCardChrome("muted");
  const folders = useFolders();
  const updateFolder = useUpdateFolder();
  const currentName =
    folders.find((f) => f.id === notice.folderId)?.name ?? notice.name;

  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(currentName);
  const skipBlurRef = useRef(false);

  const startEdit = () => {
    setDraft(currentName);
    setEditing(true);
  };

  const commitEdit = () => {
    setEditing(false);
    const name = draft.trim();
    if (!name || name === currentName) return;
    updateFolder.mutate(
      { id: notice.folderId, patch: { name } },
      { onError: (err) => notifyError(err, "重命名文件夹失败") },
    );
  };

  return (
    <div
      className="flex items-start gap-2 text-xs"
      data-testid="auto-folder-notice"
    >
      <FolderPlus size={14} className={`mt-0.5 shrink-0 ${chrome.accent}`} />
      <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-muted-foreground">{lead}</span>
        {editing ? (
          <input
            // biome-ignore lint/a11y/noAutofocus: 用户刚点「改名」，焦点就该在输入框
            autoFocus
            aria-label="文件夹名"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                e.currentTarget.blur();
              } else if (e.key === "Escape") {
                e.preventDefault();
                skipBlurRef.current = true;
                setEditing(false);
              }
            }}
            onBlur={() => {
              if (skipBlurRef.current) {
                skipBlurRef.current = false;
                return;
              }
              commitEdit();
            }}
            className="min-w-0 flex-1 rounded-lg border border-border bg-card px-1.5 py-0.5 text-xs text-foreground focus:outline-none"
          />
        ) : (
          <>
            <button
              type="button"
              onClick={() =>
                navigate("/files", filesFocusState(notice.folderId))
              }
              className="inline-flex min-w-0 items-center gap-0.5 font-medium text-foreground hover:underline"
            >
              <span className="min-w-0 truncate">{currentName}</span>
              <ChevronRight size={13} className="shrink-0" />
            </button>
            <button
              type="button"
              onClick={startEdit}
              className="shrink-0 text-muted-foreground hover:text-foreground hover:underline"
            >
              改名
            </button>
          </>
        )}
      </div>
    </div>
  );
}

/**
 * 主路径：本回合有产出文件时，落点告知是「本回合产出文件」卡的头部一行——落点和落进去
 * 的文件同处一卡，一次说清，不再单独占一张卡片。
 */
export function AutoFolderNoticeLine({ notice }: { notice: AutoFolderNotice }) {
  return (
    <div className="border-t border-border px-3 py-2">
      <AutoFolderNoticeBody notice={notice} lead="文件已存到新建的文件夹" />
    </div>
  );
}

/**
 * 边界：建了桌却没有产出文件（如 worker 写盘失败，但文件夹已建且会被后续回合复用）——
 * 产出卡不渲染，告知不能丢，独立成卡挂在正文之后。此时没有文件落进去，所以措辞只说
 * 建了文件夹，不许冒充「文件已存到」。
 */
export function AutoFolderNoticeCard({ notice }: { notice: AutoFolderNotice }) {
  const chrome = statusCardChrome("muted");
  return (
    <div
      className={`mt-3 rounded-lg border px-3 py-2 ${chrome.border} ${chrome.surface}`}
      data-testid="auto-folder-notice-card"
    >
      <AutoFolderNoticeBody notice={notice} lead="已为这次对话新建文件夹" />
    </div>
  );
}
