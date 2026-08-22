import { upsertConversationFront } from "@/hooks/useConversations";
import { copyText } from "@/lib/clipboard";
import { notifyError, notifySuccess } from "@/lib/toast";
import { ApiError, api } from "@/services/api";
import type { components } from "@/types/api.generated";
import type { NavigateFunction } from "react-router-dom";

type Schemas = components["schemas"];

/** Dev-only tape row from ``GET /v1/demo-tape`` (generated after OpenAPI dump). */
export type DemoTapeSummary = Schemas["DemoTapeSummary"];
/** Catalog when ``DEMO_TAPE_REPLAY_ENABLED`` is on. */
export type DemoTapeCatalog = Schemas["DemoTapeCatalogResponse"];
/** Prepare response — session bound, turn not started. */
export type DemoTapePrepare = Schemas["DemoTapePrepareResponse"];
/** Auto-start response — turn already running; attach via live stream. */
export type DemoTapeStart = Schemas["DemoTapeStartResponse"];

/**
 * Fetch the demo-tape catalog. Returns ``null`` when the server switch is off
 * (404) or the endpoint is unreachable — callers treat null as "hide entry".
 */
export async function fetchDemoTapeCatalog(): Promise<DemoTapeCatalog | null> {
  try {
    return await api.get<DemoTapeCatalog>("/v1/demo-tape");
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null;
    // Non-404: keep palette clean, but surface the break for local debugging
    // (silent null historically made "replay switch on but catalog broken" invisible).
    console.warn("[demo-tape] catalog fetch failed", err);
    return null;
  }
}

/** Prepare: create cloud session + bind tape; do not start a turn. */
export async function prepareDemoTape(
  tapeId: string,
): Promise<DemoTapePrepare> {
  return api.post<DemoTapePrepare>("/v1/demo-tape/prepare", {
    tape_id: tapeId,
  });
}

/** Auto-start: create cloud session + bind + begin tape turn. */
export async function startDemoTape(tapeId: string): Promise<DemoTapeStart> {
  return api.post<DemoTapeStart>("/v1/demo-tape/start", { tape_id: tapeId });
}

function openBoundConversation(
  res: { conversation_id: string; title?: string | null; user_prompt: string },
  navigate: NavigateFunction,
  opts: { messageCount: number },
): void {
  upsertConversationFront({
    id: res.conversation_id,
    title: res.title?.trim() || "演示回放",
    updatedAt: new Date().toISOString(),
    messageCount: opts.messageCount,
    lastMessagePreview:
      opts.messageCount > 0 ? res.user_prompt.slice(0, 80) || null : null,
    folderId: null,
    localContainerRootId: null,
    permissionAxes: {
      file_write: "session",
      command: "auto",
      host: "session",
    },
  });
  navigate(`/conversations/${res.conversation_id}`);
}

/**
 * Palette primary: prepare a bound empty session, navigate, suggest opening line.
 * User types/sends any message to trigger tape replay.
 */
export async function prepareDemoTapeAndOpen(
  tapeId: string,
  navigate: NavigateFunction,
): Promise<void> {
  try {
    const res = await prepareDemoTape(tapeId);
    openBoundConversation(res, navigate, { messageCount: 0 });
    const prompt = res.user_prompt.trim();
    if (prompt) {
      const copied = await copyText(prompt);
      notifySuccess("演示会话已就绪", {
        description: copied
          ? "建议开场词已复制，粘贴发送即可开播（内容任意，不影响回放）"
          : "发送任意消息即可开播；建议开场词见磁带 meta.user_prompt",
      });
    } else {
      notifySuccess("演示会话已就绪", {
        description: "发送任意消息即可开播",
      });
    }
  } catch (err) {
    notifyError(err, "准备演示回放失败");
  }
}

/**
 * Palette secondary: auto-start a tape and navigate to the live cloud conversation.
 * ConversationPage hydrate + attach/rejoin picks up the detached turn.
 */
export async function startDemoTapeAndOpen(
  tapeId: string,
  navigate: NavigateFunction,
): Promise<void> {
  try {
    const res = await startDemoTape(tapeId);
    openBoundConversation(res, navigate, { messageCount: 1 });
  } catch (err) {
    notifyError(err, "启动演示回放失败");
  }
}
