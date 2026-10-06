import { useConversations } from "@/hooks/useConversations";
import { useLlmModelProfiles } from "@/hooks/useLlmModelProfiles";
import { llmModelProfileKeys } from "@/lib/queryKeys";
import { uiGet, uiSet } from "@/lib/uiStorage";
import {
  type LlmModelProfileListResponse,
  type LlmModelProfileView,
  resolveDefaultProfile,
  setDefaultLlmModelProfile,
  updateLlmModelProfile,
} from "@/services/llmModelProfiles";
import { useConversationStore } from "@/stores/conversation";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

const MIGRATED_FLAG = "assemblyMcpMigrated";

function userAssemblies(
  profiles: LlmModelProfileListResponse,
): LlmModelProfileView[] {
  return profiles.data.filter((row) => row.kind !== "system");
}

/** Processes stay up for every server id any user assembly enables. */
export async function publishAssemblyMcpRunIds(
  rows: LlmModelProfileView[],
): Promise<void> {
  const api = window.mcpApi;
  if (!api?.setRunIds) return;
  const union = new Set<string>();
  for (const row of rows) {
    if (row.kind === "system") continue;
    for (const id of row.enabled_mcp_server_ids ?? []) union.add(id);
  }
  if (rows.every((row) => row.kind === "system")) return;
  await api.setRunIds([...union]);
}

/**
 * Once per browser profile: copy this machine's enabled plugs onto user
 * assemblies that do not name any yet, then publish the union.
 * A missing `runIds` file key is the pre-migration state. After this writes
 * it, the main process ignores the local enabled flag.
 */
export async function migrateAssemblyMcp(
  profiles: LlmModelProfileListResponse,
): Promise<LlmModelProfileView[]> {
  const api = window.mcpApi;
  let rows = userAssemblies(profiles);
  if (!api?.listServers || !api.setRunIds) return rows;
  if (uiGet<boolean>(MIGRATED_FLAG) === true) {
    await publishAssemblyMcpRunIds(rows);
    return rows;
  }
  const listed = await api.listServers();
  if (!listed.ok) return rows;
  if (rows.length === 0) {
    const preset =
      resolveDefaultProfile(profiles) ??
      profiles.data.find((row) => row.kind === "system");
    if (preset) {
      const created = await setDefaultLlmModelProfile(preset.id);
      rows = [created];
    }
  }
  const enabled = listed.servers
    .filter((server) => server.enabled)
    .map((server) => server.id);
  if (enabled.length > 0) {
    const next: LlmModelProfileView[] = [];
    for (const row of rows) {
      const ids = row.enabled_mcp_server_ids ?? [];
      if (ids.length > 0) {
        next.push(row);
        continue;
      }
      next.push(
        await updateLlmModelProfile(row.id, {
          enabled_mcp_server_ids: enabled,
        }),
      );
    }
    rows = next;
  }
  uiSet(MIGRATED_FLAG, true);
  await publishAssemblyMcpRunIds(rows);
  return rows;
}

export function AssemblyMcpBridge() {
  const profiles = useLlmModelProfiles();
  const queryClient = useQueryClient();
  const started = useRef(false);

  useEffect(() => {
    if (started.current || !profiles.data) return;
    if (!window.mcpApi?.setRunIds) return;
    started.current = true;
    void migrateAssemblyMcp(profiles.data)
      .then(() =>
        queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list }),
      )
      .catch(() => {
        started.current = false;
      });
  }, [profiles.data, queryClient]);

  return null;
}

/** Conversation's assembly when it is a real row; otherwise the star. */
export function useAssemblyPlugController() {
  const profiles = useLlmModelProfiles();
  const conversationId = useConversationStore((s) => s.currentConversationId);
  const conversations = useConversations();
  const queryClient = useQueryClient();

  const targetId = useMemo(() => {
    const rows = profiles.data?.data ?? [];
    const userRow = (id: string | null | undefined) =>
      rows.find((row) => row.id === id && row.kind !== "system");
    const pinned = conversations.find(
      (row) => row.id === conversationId,
    )?.assemblyId;
    return (
      userRow(pinned)?.id ??
      userRow(resolveDefaultProfile(profiles.data)?.id)?.id ??
      userRow(profiles.data?.default_assembly_id)?.id ??
      null
    );
  }, [profiles.data, conversations, conversationId]);

  const enabledIds = useMemo(() => {
    const row = profiles.data?.data.find((item) => item.id === targetId);
    return new Set(row?.enabled_mcp_server_ids ?? []);
  }, [profiles.data, targetId]);

  const toggle = async (serverId: string, on: boolean) => {
    if (!targetId || !profiles.data) return;
    const row = profiles.data.data.find((item) => item.id === targetId);
    const next = new Set(row?.enabled_mcp_server_ids ?? []);
    if (on) next.add(serverId);
    else next.delete(serverId);
    const updated = await updateLlmModelProfile(targetId, {
      enabled_mcp_server_ids: [...next],
    });
    const rows = profiles.data.data.map((item) =>
      item.id === updated.id ? updated : item,
    );
    await publishAssemblyMcpRunIds(rows);
    await queryClient.invalidateQueries({ queryKey: llmModelProfileKeys.list });
  };

  return { targetId, enabledIds, toggle };
}
