/**
 * 展示层：剥掉一段过程正文开头的空白。
 *
 * 模型 / 中转常在 `output_text` 前垫 2–6 个空格；CommonMark 把行首 ≥4 空格收成
 * 缩进代码块，于是中间段散文会画出「text」代码框。只 `trimStart`（直播未完成时
 * 不能 `trim` 掉尾部）。不改 journal / 落库 / 下一轮历史。围栏代码块以 ` ``` `
 * 开头，不受影响。
 */
export function trimMarkdownLeadingPad(text: string): string {
  return text.trimStart();
}
