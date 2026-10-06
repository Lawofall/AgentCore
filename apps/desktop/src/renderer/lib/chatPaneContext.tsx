import { type ReactNode, createContext, useContext } from "react";

/**
 * 一栏聊天的对话 id。`undefined` = 没包在栏里，跟焦点走。
 * `null` = 这一栏是草稿，即使焦点在另一场。
 *
 * 不读会话仓库：仓库的选择器和这一栏的范围互相引用会绕成环。
 */
const ChatPaneContext = createContext<string | null | undefined>(undefined);

export function ChatPaneProvider({
  id,
  children,
}: {
  id: string | null;
  children: ReactNode;
}) {
  return (
    <ChatPaneContext.Provider value={id}>{children}</ChatPaneContext.Provider>
  );
}

export function useChatPaneScope(): string | null | undefined {
  return useContext(ChatPaneContext);
}
