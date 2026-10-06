import { APP_PATHS } from "@/pages/toolbox/manual/paths";

export type ToolboxTab =
  | "overview"
  | "model"
  | "tools"
  | "envelope"
  | "prompts"
  | "plugs";

export type AssemblyHome = "toolbox" | "settings";

/** Opt-in on a tool or envelope shelf while a conversation is open. */
export const ASSEMBLY_TURN_HINT =
  "正在生成的这一轮不换，从下一次进入回合生效。";

export function hasLocalMcp(): boolean {
  return typeof window !== "undefined" && Boolean(window.mcpApi);
}

export function assemblyHomeFromPath(pathname: string): AssemblyHome {
  return pathname.startsWith("/more/model") ? "settings" : "toolbox";
}

/** One shelf. Old section routes land on this path, with a hash when they had a section. */
export function assemblyShelfPath(
  tab: ToolboxTab | null,
  home: AssemblyHome,
): string {
  const base = home === "settings" ? "/more/model" : APP_PATHS.toolbox.root;
  if (!tab || tab === "overview") return base;
  const hash = tab === "prompts" ? "prompts" : tab === "plugs" ? "plugs" : tab;
  return `${base}#${hash}`;
}

export function toolboxTabFromPath(pathname: string): ToolboxTab | null {
  if (
    pathname === APP_PATHS.toolbox.overview ||
    pathname === "/more/model/overview"
  ) {
    return "overview";
  }
  if (
    pathname === APP_PATHS.toolbox.model ||
    pathname === "/more/model/model"
  ) {
    return "model";
  }
  if (
    pathname === APP_PATHS.toolbox.toolSwitches ||
    pathname === "/more/model/tools"
  ) {
    return "tools";
  }
  if (
    pathname === APP_PATHS.toolbox.envelope ||
    pathname === "/more/model/envelope"
  ) {
    return "envelope";
  }
  if (
    pathname === APP_PATHS.toolbox.mine.skills ||
    pathname === "/more/model/prompts"
  ) {
    return "prompts";
  }
  if (pathname === APP_PATHS.toolbox.mcp || pathname === "/more/model/plugs") {
    return "plugs";
  }
  return null;
}
