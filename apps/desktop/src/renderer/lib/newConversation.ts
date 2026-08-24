import { useConversationStore } from "@/stores/conversation";
import { useFoldersStore } from "@/stores/folders";
import { useSidePanelStore } from "@/stores/sidePanel";
import type { NavigateFunction } from "react-router-dom";

export type NewConversationOpts = { cloud?: boolean; local?: boolean };

/**
 * 草稿 intent + 关右坞 + 切草稿会话（null）。供 navigate / hash 两条入口共用。
 */
function prepareNewConversationDraft(
  folderId?: string | null,
  opts?: NewConversationOpts,
): void {
  const foldersStore = useFoldersStore.getState();
  if (opts?.local) {
    // §7.2：禁新建本机草稿；改导云路径（存量会话续跑不经本入口）。
    foldersStore.setDraftWorkspaceIntent({ kind: "quick_cloud" });
  } else if (opts?.cloud) {
    foldersStore.setDraftWorkspaceIntent({ kind: "quick_cloud" });
  } else if (folderId) {
    foldersStore.setDraftWorkspaceIntent({
      kind: "folder",
      folderId,
    });
  } else {
    foldersStore.resetDraftWorkspaceIntent();
  }
  useSidePanelStore.getState().closePanel();
  useConversationStore.getState().switchConversation(null);
}

/**
 * 开启一个全新的草稿对话并跳到 `/`。草稿只活在 store 里（不落库），首条消息发送时
 * 才由 MessageInput 真正在后端 create会话。
 *
 * - 不传 folderId：默认云端裸聊草稿（桌面裸聊默认切云 §八.7）
 * - 传 folderId：项目草稿（出生定终身继承项目工作区；含侧栏点已有未迁 local）
 * - `opts.cloud`：显式云端草稿（与默认同）
 * - `opts.local`：§7.2 已废——改导云草稿，不再造 `quick_local`
 *
 * N4-A：离线仍可进入空白草稿页（导航与创建解耦）；发送由 composer 硬禁
 * （`ComposerConnectionNotice` + `useComposerSend`）。
 *
 * 草稿不可用右坞：进入时强制关闭（不出现、也不能再打开）。
 */
export function startNewConversation(
  navigate: NavigateFunction,
  folderId?: string | null,
  opts?: NewConversationOpts,
): void {
  prepareNewConversationDraft(folderId, opts);
  navigate("/");
}

/**
 * 无 router 依赖的草稿入口（toast action 等）：与 {@link startNewConversation} 同语义，
 * 用 hash 导航到 `/`（createHashRouter 与 `<Link>` 等价）。
 */
export function openDraftConversation(
  folderId?: string | null,
  opts?: NewConversationOpts,
): void {
  prepareNewConversationDraft(folderId, opts);
  window.location.hash = "/";
}
