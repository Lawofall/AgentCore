import { resolveToolWireStatus } from "@/lib/channelRedirect";
import { isVerifyBudgetExceeded } from "./verifyBudget";

const EXEC_TOOLS = new Set(["run", "code_execute", "test_run", "terminal"]);

const LOOKUP_TOOLS = new Set([
  "file_read",
  "file_list",
  "str_replace",
  "glob",
  "grep",
  "file_delete",
  "file_move",
  "file_copy",
  "web_fetch",
  "read_conversation",
  "search_conversations",
  "read_folder_file",
  "list_folder_dir",
]);

/** Collapsed-row / folded-group word for a tool that didn't work. Uncolored.
 *  Redirect and verify-incomplete are not this face. */
export function toolRowFaultLabel(step: {
  tool_name: string;
  status: string;
  failure?: { code?: string | null } | null;
  display?: unknown;
}): string | null {
  const status = resolveToolWireStatus(step.status, step.failure);
  if (status !== "error") return null;
  if (isVerifyBudgetExceeded(step.display)) return null;
  const code = (step.failure?.code ?? "").toLowerCase();
  if (EXEC_TOOLS.has(step.tool_name)) return "未通过";
  if (code === "not_found" || LOOKUP_TOOLS.has(step.tool_name)) return "未找到";
  return "未完成";
}

/** File / lookup misses already say「未找到」on the row — skip the extra sentence. */
export function isSelfExplanatoryLookupError(step: {
  tool_name: string;
  status: string;
  failure?: { code?: string | null } | null;
  display?: unknown;
}): boolean {
  return (
    LOOKUP_TOOLS.has(step.tool_name) && toolRowFaultLabel(step) === "未找到"
  );
}

/** One word for a collapsed group. Mixed kinds collapse to 未完成. */
export function toolGroupFaultLabel(
  tools: Array<{
    tool_name: string;
    status: string;
    failure?: { code?: string | null } | null;
    display?: unknown;
  }>,
): string | null {
  const words = new Set<string>();
  for (const t of tools) {
    const word = toolRowFaultLabel(t);
    if (word) words.add(word);
  }
  if (words.size === 0) return null;
  if (words.size === 1) return [...words][0] ?? null;
  return "未完成";
}
