import { skillBodyFromContent } from "@/services/skillCatalog";

/** 上架对话框 · 已写介绍时。 */
export const PUBLISH_READY_HINT =
  "装进去之后，CEO 靠一句话介绍决定什么时候翻开。再选一个分组，别人按这个逛货架。";

/** 上架拦缺介绍（与 API 400 同句）。 */
export const PUBLISH_MISSING_INTRO =
  "先写一句话介绍：干什么、什么时候该翻开。没这句，别人装了 CEO 也找不到。";

/** 上架拦空正文（与 API 400 同句）。 */
export const PUBLISH_MISSING_BODY = "先写正文。空的做法上架没有用。";

/** 市场详情 · 介绍句旁。 */
export const MARKET_CATALOG_CAPTION = "装进去之后，CEO 靠这句决定什么时候翻开";

/** `content` 空串视为尚未加载，不误判成没有正文。 */
export function publishBlockReason(
  description: string,
  content: string | undefined,
): string | null {
  if (!description.trim()) return PUBLISH_MISSING_INTRO;
  if (content == null || !content.trim()) return null;
  if (!skillBodyFromContent(content).trim()) return PUBLISH_MISSING_BODY;
  return null;
}
