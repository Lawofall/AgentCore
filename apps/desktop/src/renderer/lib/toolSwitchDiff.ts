/**
 * How this conversation's deny list differs from the account seed.
 * Null when the two sets are the same — the composer stays quiet.
 */
export function conversationToolDiffLabel(
  conversationDisabled: readonly string[],
  accountDisabled: readonly string[],
): string | null {
  const conversation = new Set(conversationDisabled);
  const account = new Set(accountDisabled);
  let onlyConversation = 0;
  let onlyAccount = 0;
  for (const id of conversation) {
    if (!account.has(id)) onlyConversation += 1;
  }
  for (const id of account) {
    if (!conversation.has(id)) onlyAccount += 1;
  }
  if (onlyConversation === 0 && onlyAccount === 0) return null;
  if (onlyAccount === 0) return `这场关了 ${onlyConversation} 样`;
  if (onlyConversation === 0) return `这场开了 ${onlyAccount} 样`;
  return `这场和默认差 ${onlyConversation + onlyAccount} 样`;
}
