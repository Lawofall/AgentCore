import { toWorkspaceRelPath } from "@shared/workspace-path";

const IMAGE_EXT = /\.(png|jpe?g|gif|webp|svg|bmp|ico|avif|heic|heif)$/i;

const BLOCKED_SCHEME = /^(?:https?:|data:|blob:|javascript:|file:)/i;

/**
 * PI-001：compare fence 只许工作区相对图片路径，禁止任意外链或 scheme URL。
 * 返回规范化后的工作区相对路径；不合法则 null。
 */
export function resolveWorkspaceImageRef(raw: string): string | null {
  const trimmed = raw.trim();
  if (!trimmed || BLOCKED_SCHEME.test(trimmed) || trimmed.startsWith("//")) {
    return null;
  }
  if (trimmed.includes("://")) return null;
  const rel = toWorkspaceRelPath(trimmed);
  // After stripping `/workspace/…`, leftover absolute / drive paths are not
  // workspace-relative (toWorkspaceRelPath leaves `/etc/…` and `C:/…` intact).
  if (
    !rel ||
    rel.includes("..") ||
    rel.startsWith("/") ||
    /^[a-zA-Z]:/.test(rel)
  ) {
    return null;
  }
  if (!IMAGE_EXT.test(rel.split("/").pop() ?? "")) return null;
  return rel;
}
