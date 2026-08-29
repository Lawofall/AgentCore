import type { GrantSessionWellKnown } from "@shared/ipc-contract";

/** Optional hints forwarded to `fs:grantSessionReadonlyRoot`. */
export type GrantFolderHints = {
  /** Absolute local directory (C1-wide mount path transport). */
  path?: string;
  wellKnown?: GrantSessionWellKnown;
  targetName?: string;
};

const WELL_KNOWN = new Set<GrantSessionWellKnown>([
  "desktop",
  "downloads",
  "documents",
]);

/** Card-facing well_known labels (never abs). */
export const WELL_KNOWN_LABEL_ZH: Record<GrantSessionWellKnown, string> = {
  desktop: "桌面",
  downloads: "下载",
  documents: "文档",
};

/** Organize confirm shell — not 「选文件夹」 / picker framing. */
export const ORGANIZE_CONFIRM_CAPTION = "整理确认 · 允许后可写回";
export const ORGANIZE_CONFIRM_CTA = "允许整理";
export const ATTACH_CONFIRM_CAPTION = "加入本对话 · 允许后可改可覆盖";
export const ATTACH_CONFIRM_CTA = "允许改这个目录";

/**
 * 人话框口头同意短允许表（精确匹配，非意图分类 / 不扫长文）。
 * 命中且该题 listed 未勾选 → 与点确认 CTA 同一次真 grant；未命中保持原 compose。
 * 两表禁共用「可以/允许」等泛词：同题两选项时泛词不得授 attach_rw。
 * 认本题人话（按 question.id），不是整卡一句。
 * → 双模式工作区 §六口头同意闭环
 */
const ORGANIZE_ORAL_CONSENT = new Set([
  "可以",
  "允许",
  "同意",
  "好的",
  "好",
  "可以整理",
  "允许整理",
  "同意整理",
  "升级可整理",
]);

/** Strip trailing sentence punctuation so 「可以。」仍命中短表. */
function normalizeOralConsentText(raw: string): string {
  return raw
    .trim()
    .replace(/[\s。．.！!？?，,、；;：:]+$/u, "")
    .trim();
}

const ATTACH_ORAL_CONSENT = new Set([
  "可以改",
  "允许改",
  "同意改",
  "可以加入",
  "允许写入",
  "可以覆盖",
  "允许覆盖",
  ATTACH_CONFIRM_CTA,
]);

/** True when人话框提交字面命中整理口头同意短允许表. */
export function isOrganizeOralConsent(text: string): boolean {
  const normalized = normalizeOralConsentText(text);
  if (!normalized) return false;
  return ORGANIZE_ORAL_CONSENT.has(normalized);
}

export function isAttachOralConsent(text: string): boolean {
  const normalized = normalizeOralConsentText(text);
  if (!normalized) return false;
  return ATTACH_ORAL_CONSENT.has(normalized);
}

export function isGrantOralConsent(
  action: string | undefined,
  text: string,
): boolean {
  if (action === "grant_attach_folder") return isAttachOralConsent(text);
  if (action === "grant_organize_folder") return isOrganizeOralConsent(text);
  return false;
}

/**
 * 人话框 → 至多一个 listed grant_*。0 或 ≥2 命中都不履约（同题两档时泛词不得
 * 因选项顺序授成 attach_rw）。
 */
export function pickOralGrantOption<T extends { action?: string }>(
  options: T[],
  note: string,
): T | undefined {
  const matches = options.filter((o) => isGrantOralConsent(o.action, note));
  if (matches.length !== 1) return undefined;
  return matches[0];
}

/**
 * Map AskOption grant_* wire fields to IPC camelCase hints.
 * Callers only invoke this for `grant_organize_folder` / `grant_attach_folder`
 * (not organize_plan rows): optional `path` is the C1 mount-only abs transport
 * and must match card preview — forward it with well_known / target_name.
 * Returns undefined when no resolve hint is present (blank grant → not_found).
 */
export function grantHintsFromAskOption(opt: {
  path?: string;
  well_known?: string;
  target_name?: string;
}): GrantFolderHints | undefined {
  const path =
    typeof opt.path === "string" && opt.path.trim()
      ? opt.path.trim()
      : undefined;
  const wellKnown = WELL_KNOWN.has(opt.well_known as GrantSessionWellKnown)
    ? (opt.well_known as GrantSessionWellKnown)
    : undefined;
  const trimmed =
    typeof opt.target_name === "string" ? opt.target_name.trim() : "";
  const targetName = trimmed || undefined;
  if (!path && !wellKnown && !targetName) return undefined;
  return {
    ...(path ? { path } : {}),
    ...(wellKnown ? { wellKnown } : {}),
    ...(targetName ? { targetName } : {}),
  };
}

function basenameHint(path: string): string | undefined {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean);
  const base = parts[parts.length - 1];
  return base || undefined;
}

/**
 * Synthesize organize-card target label before allow (C1 phase 3).
 * Prefer well_known+target_name →「桌面 › 咨询」; path → basename only; never full abs.
 */
export function previewOrganizeTargetLabel(input: {
  path?: string;
  well_known?: string;
  wellKnown?: GrantSessionWellKnown;
  target_name?: string;
  targetName?: string;
}): string | undefined {
  const path = typeof input.path === "string" ? input.path.trim() : "";
  if (path) return basenameHint(path);

  const wellKnownRaw = input.wellKnown ?? input.well_known;
  const wellKnown = WELL_KNOWN.has(wellKnownRaw as GrantSessionWellKnown)
    ? (wellKnownRaw as GrantSessionWellKnown)
    : undefined;
  const target =
    (typeof input.targetName === "string" ? input.targetName.trim() : "") ||
    (typeof input.target_name === "string" ? input.target_name.trim() : "") ||
    undefined;

  if (wellKnown && target) {
    return `${WELL_KNOWN_LABEL_ZH[wellKnown]} › ${target}`;
  }
  if (wellKnown) return WELL_KNOWN_LABEL_ZH[wellKnown];
  if (target) return target;
  return undefined;
}

/**
 * Detail under `grant_organize_folder` option: always show「将整理：…」before allow.
 * Ordinary options return undefined — generic asks are one line; do not pass
 * model `detail` through (that subtitle is dedicated-card only).
 */
export function organizeConfirmDetail(opt: {
  action?: string;
  detail?: string;
  path?: string;
  well_known?: string;
  target_name?: string;
}): string | undefined {
  if (
    opt.action !== "grant_organize_folder" &&
    opt.action !== "grant_attach_folder"
  )
    return undefined;
  const label = previewOrganizeTargetLabel(opt);
  if (opt.action === "grant_attach_folder") {
    return label
      ? `将可读写加入：${label}`
      : "将本机目录加入本对话（可改可覆盖）";
  }
  return label ? `将整理：${label}` : "将整理本机目录";
}
