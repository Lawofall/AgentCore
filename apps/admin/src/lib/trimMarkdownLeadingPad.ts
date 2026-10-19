/**
 * 展示层：剥掉一段过程正文开头的空白。
 *
 * 与桌面 `trimMarkdownLeadingPad` 同口径：行首 ≥4 空格会被 CommonMark 收成缩进
 * 代码块。只 `trimStart`，不改 journal。
 */
export function trimMarkdownLeadingPad(text: string): string {
  return text.trimStart();
}
