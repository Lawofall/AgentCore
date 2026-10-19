import { SettingsAsync } from "@/components/settings";
import { Button, Card, ConfirmDialog, PageHeader } from "@/components/ui";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import {
  type SearchConnectionResponse,
  type SearchEngine,
  connectDeepSeekFromProvider,
  disconnectDeepSeek,
  getSearchConnection,
  selectSearchEngine,
} from "@/services/searchConnection";
import { Check } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { Link } from "react-router-dom";

type Slot = NonNullable<SearchConnectionResponse["deepseek"]>;

/**
 * 设置 · 联网搜索（/more/search）。
 *
 * 账号级选用，和这场用哪份能力无关。平台搜索始终在。DeepSeek 一个服务位：
 * 用已连接的官方 DeepSeek 服务商 Key。没有服务商则去设置 · 服务商。
 */
export function SearchConnectionSettings() {
  const [view, setView] = useState<SearchConnectionResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [pendingDisconnect, setPendingDisconnect] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getSearchConnection()
      .then((next) => {
        if (!cancelled) setView(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) setLoadError(loadFailure(err));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const choose = (engine: SearchEngine): void => {
    if (busy || view == null || engine === view.selected) return;
    setBusy(true);
    selectSearchEngine(engine)
      .then(setView)
      .catch((err: unknown) => notifyError(err, "切换搜索失败"))
      .finally(() => setBusy(false));
  };

  const connectFromProvider = (): void => {
    if (busy) return;
    setBusy(true);
    connectDeepSeekFromProvider()
      .then(setView)
      .catch((err: unknown) => notifyError(err, "连接 DeepSeek 失败"))
      .finally(() => setBusy(false));
  };

  return (
    <div>
      <PageHeader title="联网搜索" />
      {view == null ? (
        <SettingsAsync
          className="mt-6"
          loading={loadError == null}
          error={loadError ?? undefined}
        />
      ) : (
        <div className="mt-6 space-y-4">
          <PlatformCard
            selected={view.selected === "platform"}
            quota={view.quota}
            busy={busy}
            onSelect={() => choose("platform")}
          />
          <EngineCard
            label="DeepSeek"
            slot={view.deepseek ?? null}
            providerLabel={view.official_deepseek_provider?.label ?? null}
            selected={view.selected === "deepseek"}
            busy={busy}
            onSelect={() => choose("deepseek")}
            onConnect={connectFromProvider}
            onDisconnect={() => setPendingDisconnect(true)}
          />
        </div>
      )}
      <ConfirmDialog
        open={pendingDisconnect}
        onOpenChange={(open) => {
          if (!open) setPendingDisconnect(false);
        }}
        title="断开这个搜索服务？"
        description={
          view?.selected === "deepseek"
            ? "断开后，联网搜索改回平台搜索。"
            : "当前正在使用的搜索不变。"
        }
        confirmLabel="断开"
        tone="danger"
        busy={busy}
        onConfirm={() => {
          setBusy(true);
          disconnectDeepSeek()
            .then((next) => {
              setPendingDisconnect(false);
              setView(next);
            })
            .catch((err: unknown) => notifyError(err, "断开搜索服务失败"))
            .finally(() => setBusy(false));
        }}
      />
    </div>
  );
}

function PlatformCard({
  selected,
  quota,
  busy,
  onSelect,
}: {
  selected: boolean;
  quota: SearchConnectionResponse["quota"];
  busy: boolean;
  onSelect: () => void;
}) {
  const description = `今日 ${countText(quota.daily_used, quota.daily_limit)} · 本月 ${countText(quota.monthly_used, quota.monthly_limit)}`;
  return (
    <EngineSurface
      label="平台搜索"
      description={description}
      selected={selected}
      actions={
        selected ? null : (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            aria-label="选用平台搜索"
            onClick={onSelect}
          >
            选用
          </Button>
        )
      }
    />
  );
}

function EngineCard({
  label,
  slot,
  providerLabel,
  selected,
  busy,
  onSelect,
  onConnect,
  onDisconnect,
}: {
  label: string;
  slot: Slot | null;
  providerLabel: string | null;
  selected: boolean;
  busy: boolean;
  onSelect: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  const reconnect = slot?.status === "needs_reconnect";
  const hasProvider = providerLabel != null;
  const emptyHint = hasProvider
    ? `用已连接的服务商「${providerLabel}」的 Key`
    : "到设置 · 服务商连接官方 DeepSeek";
  const actions =
    slot == null ? (
      hasProvider ? (
        <Button
          type="button"
          size="sm"
          disabled={busy}
          aria-label={`连接 ${label}`}
          onClick={onConnect}
        >
          连接
        </Button>
      ) : (
        <ProvidersLink />
      )
    ) : (
      <>
        {!selected && !reconnect ? (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            aria-label={`选用 ${label}`}
            onClick={onSelect}
          >
            选用
          </Button>
        ) : null}
        {reconnect ? (
          hasProvider ? (
            <Button
              type="button"
              variant="neutral"
              size="sm"
              disabled={busy}
              aria-label={`重新连接 ${label}`}
              onClick={onConnect}
            >
              重新连接
            </Button>
          ) : (
            <ProvidersLink />
          )
        ) : null}
        <Button
          type="button"
          variant="neutral"
          size="sm"
          disabled={busy}
          aria-label={`断开 ${label}`}
          onClick={onDisconnect}
        >
          断开
        </Button>
      </>
    );

  return (
    <EngineSurface
      label={label}
      description={
        slot == null ? emptyHint : slotDescription(slot, providerLabel)
      }
      selected={selected}
      actions={actions}
    />
  );
}

function ProvidersLink() {
  return (
    <Link
      to="/more/providers"
      className="inline-flex h-8 items-center rounded-lg bg-primary px-3 text-xs font-medium text-primary-foreground hover:bg-primary/90"
      aria-label="去服务商"
    >
      去服务商
    </Link>
  );
}

function EngineSurface({
  label,
  description,
  selected,
  actions,
}: {
  label: string;
  description: string;
  selected: boolean;
  actions?: ReactNode;
}) {
  return (
    <Card
      className={cn("px-4 py-3", selected && "border-primary/40 bg-primary/5")}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <div className="flex items-center gap-2">
            <p className="truncate text-sm font-medium text-foreground">
              {label}
            </p>
            {selected ? (
              <span
                role="img"
                className="inline-flex shrink-0 text-primary"
                aria-label={`当前使用 ${label}`}
              >
                <Check size={16} aria-hidden />
              </span>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground">{description}</p>
        </div>
      </div>
      {actions ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">{actions}</div>
      ) : null}
    </Card>
  );
}

function slotDescription(slot: Slot, providerLabel: string | null): string {
  if (slot.status === "needs_reconnect") {
    return slot.message ? `需要重新连接 · ${slot.message}` : "需要重新连接";
  }
  return providerLabel
    ? `来自已连接的服务商 · ${providerLabel}`
    : "来自已连接的服务商";
}

function countText(used: number, limit: number): string {
  return limit <= 0 ? `${used}/不限` : `${used}/${limit}`;
}

function loadFailure(err: unknown): string {
  if (err instanceof ApiError && err.serverMessage?.trim()) {
    return err.serverMessage;
  }
  return "读取联网搜索设置失败";
}
