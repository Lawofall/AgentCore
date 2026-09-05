/**
 * Composer「在哪工作」通道记忆。
 *
 * `local_traditional` = 本机（桌面默认：本地对话用本地引擎）；
 * `cloud` = 云端对话（并列；网页/手机无本机盘时的唯一通道）。
 * 仅桌面 UI 持久化（uiStorage）；有记忆跟上次；无记忆桌面 → 本机，其它 → 云。
 */

import { hasLocalFiles } from "@/lib/capabilities";
import { uiGet, uiSet } from "@/lib/uiStorage";

const STORAGE_KEY = "composer-channel";

export type ComposerChannel = "cloud" | "local_traditional";

function parseChannel(raw: unknown): ComposerChannel | null {
  if (raw === "cloud" || raw === "local_traditional") return raw;
  return null;
}

function unsetDefault(): ComposerChannel {
  return hasLocalFiles() ? "local_traditional" : "cloud";
}

export function storedComposerChannelPreference(): ComposerChannel | null {
  return parseChannel(uiGet<unknown>(STORAGE_KEY));
}

/** 读上次通道；无记忆或非法值 → 桌面本机 / 其它云。 */
export function getComposerChannelPreference(): ComposerChannel {
  return storedComposerChannelPreference() ?? unsetDefault();
}

/** 记上次通道（cloud | local_traditional）。 */
export function setComposerChannelPreference(channel: ComposerChannel): void {
  uiSet(STORAGE_KEY, channel);
}
