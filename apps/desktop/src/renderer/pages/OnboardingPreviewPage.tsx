import { MessageInput } from "@/components/chat/MessageInput";
import { DraftEmptyState } from "@/components/onboarding/DraftEmptyState";
import { ONBOARDING_PREVIEW_SCENES } from "@/preview/onboardingScenes";
import { draftKeyFor, useComposerDraftStore } from "@/stores/composer";
import { useConversationStore } from "@/stores/conversation";
import { FlaskConical } from "lucide-react";
import { useEffect } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * Hidden preview (`#/preview/onboarding`) for draft empty states and composer
 * generating variants. Deep-link: `#/preview/onboarding?s=empty-starter-chips` 等.
 *
 * Empty scenes mirror ChatView placement: starter_chips / returning = greeting
 * (+ chips) + composer as one centered block（平台代付后无 needs_key 态）。
 */
export function OnboardingPreviewPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const scenes = ONBOARDING_PREVIEW_SCENES;
  const requested = searchParams.get("s");
  const current = scenes.find((s) => s.id === requested) ?? scenes[0] ?? null;
  const selected = current?.id ?? null;

  const select = (id: string) => setSearchParams({ s: id }, { replace: true });

  const genVariant =
    current?.kind === "composer-generating-bar"
      ? "bar"
      : current?.kind === "composer-generating-card"
        ? "card"
        : null;

  // 生成中再发态：isGenerating=true + 草稿 → 插队 / 排队 / 停止并存。
  // 切走/卸载时复位，不污染其它预览场景。
  useEffect(() => {
    if (!genVariant) return;
    const key = draftKeyFor(null);
    const draft = useComposerDraftStore.getState();
    const conv = useConversationStore.getState();
    draft.setValue(key, "顺便把上周的预算表也一起产出");
    conv.setGenerating(true, null);
    return () => {
      draft.setValue(key, "");
      conv.setGenerating(false, null);
    };
  }, [genVariant]);

  return (
    <div
      className="flex h-full min-h-0 w-full"
      data-preview-onboarding={selected ?? ""}
    >
      <aside className="flex w-80 shrink-0 flex-col border-r border-border">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <FlaskConical size={18} className="shrink-0 text-primary" />
          <div className="min-h-0 min-w-0 flex-1">
            <h1 className="truncate text-base font-semibold text-foreground">
              首启体验 · 预览
            </h1>
            <p className="text-xs text-muted-foreground">
              {scenes.length} 个场景 · 离线自检
            </p>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-2">
          <ul className="space-y-0.5">
            {scenes.map((s) => (
              <li key={s.id}>
                <button
                  type="button"
                  onClick={() => select(s.id)}
                  className={`w-full rounded-lg px-3 py-2.5 text-left ${
                    selected === s.id
                      ? "bg-accent text-foreground"
                      : "text-muted-foreground hover:bg-accent hover:text-foreground"
                  }`}
                >
                  <span className="block truncate text-sm font-medium">
                    {s.title}
                  </span>
                  <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                    {s.id}
                  </span>
                  <span className="mt-1 block text-xs leading-snug text-muted-foreground">
                    {s.intent}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </aside>

      <div className="@container/chat relative flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden bg-background">
        {(current?.kind === "empty-starter-chips" ||
          current?.kind === "empty-returning") && (
          <div
            className="absolute inset-0 z-10 flex items-center justify-center overflow-y-auto py-10"
            data-composer-dock="center"
          >
            <div className="relative mx-auto w-full max-w-3xl">
              <div className="absolute inset-x-0 bottom-full mb-6">
                <DraftEmptyState
                  previewKind={
                    current.kind === "empty-starter-chips"
                      ? "starter_chips"
                      : "returning"
                  }
                />
              </div>
              <MessageInput className="px-4 pb-2" />
            </div>
          </div>
        )}
        {genVariant === "bar" && (
          <div
            className="relative flex h-full min-h-0 flex-col"
            data-composer-dock="bottom"
          >
            <div className="flex min-h-0 flex-1 items-center justify-center px-6 py-10 text-center text-sm text-muted-foreground">
              回合执行中：有草稿时「插队 / 排队 /
              停止」并存；Enter=排队；Ctrl/Cmd+Enter=插队
            </div>
            <div className="mx-auto w-full max-w-3xl">
              <MessageInput variant="bar" />
            </div>
          </div>
        )}
        {genVariant === "card" && (
          <div
            className="absolute inset-0 z-10 flex items-center justify-center overflow-y-auto py-10"
            data-composer-dock="center"
          >
            <div className="mx-auto flex w-full max-w-3xl flex-col">
              <div className="px-4 pb-2 text-center text-sm text-muted-foreground">
                回合执行中：聊天居中输入同样「插队 / 排队 /
                停止」并存；无草稿仅停止
              </div>
              <MessageInput className="px-4 pb-2 pt-4" variant="card" />
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
