/** Official recipes are catalogs, not editors. 极简 is picked in the composer. */

export const OFFICIAL_FULL_READONLY = "完整不能改。要自己关工具，点新建。";
export const OFFICIAL_CHAT_READONLY =
  "这场只聊天，不能改。要自己关工具，点新建。";

const PICKER_BLURB: Record<string, string> = {
  full: "能改文件、跑命令、上网。",
  chat: "这场只聊天。",
};

export function assemblyPickerBlurb(
  recipe: string | null | undefined,
): string | undefined {
  if (!recipe) return undefined;
  return PICKER_BLURB[recipe];
}

export function officialHint(
  profile:
    | {
        kind?: string;
        recipe?: string | null;
      }
    | null
    | undefined,
): string | null {
  if (profile?.kind !== "system") return null;
  return profile.recipe === "chat"
    ? OFFICIAL_CHAT_READONLY
    : OFFICIAL_FULL_READONLY;
}

export function isToolboxCapsule(row: {
  kind?: string;
  recipe?: string | null;
}): boolean {
  if (row.kind === "user") return true;
  return row.kind === "system" && row.recipe !== "chat";
}

export function assemblyIsOfficial(
  profile: { kind?: string } | null | undefined,
): boolean {
  return profile?.kind === "system";
}

/** 极简目录：这场只聊天，输入区「统筹」没有下一步。 */
export function assemblyIsChatOnly(
  profile: { recipe?: string | null } | null | undefined,
): boolean {
  return profile?.recipe === "chat";
}
