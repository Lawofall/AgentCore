/**
 * 空草稿居中判定 — 纯客户端。
 *
 * 平台代付、开箱即用：不存在「先接入模型才能对话」的门。空草稿只留问候 + 输入框，
 * 新老用户同一张脸。BYOK 降级为「设置·模型配置」里的可选升级。
 */

/**
 * 空草稿态是否把对话输入框与引导合成视口中央块。
 * 「（草稿态 ∨ 已落库但确定 0 消息）∧ 无消息」——平台代付后 keyless 亦居中，无接入门例外。
 *
 * `isDraft`（`conversationId === null`）是主闸门：已落库的对话切换时会先经历一个
 * 「消息尚未异步加载完」的空窗口，若只看 `!hasMessages` 会把它误判为居中欢迎态，
 * 导致输入框「先弹到中间、加载完再飞回底栏」的跳动。
 *
 * `knownEmptyPersisted` 覆盖「演示磁带 prepare 绑定的空会话」这类**已落库却确定 0 消息**的场景：
 * 它由会话元数据 `messageCount === 0` 推出——确定为空、无需等历史异步加载，故不会重蹈上述抖动
 * （有消息的会话 messageCount>0、历史加载中未入列表的会话元数据查不到，二者皆不命中）。
 */
export function shouldCenterDraftComposer(input: {
  isDraft: boolean;
  hasMessages: boolean;
  knownEmptyPersisted?: boolean;
}): boolean {
  if (input.hasMessages) return false;
  return input.isDraft || input.knownEmptyPersisted === true;
}
