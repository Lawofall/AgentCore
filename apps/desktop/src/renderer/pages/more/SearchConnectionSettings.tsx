import {
  SettingField,
  SettingRow,
  SettingsSection,
} from "@/components/settings";
import { Button, ConfirmDialog, Input } from "@/components/ui";
import { notifyError } from "@/lib/toast";
import { ApiError } from "@/services/api";
import {
  type OwnSearchEngine,
  type SearchConnectionResponse,
  type SearchEngine,
  connectCleverSee,
  connectSearxng,
  disconnectSearchEngine,
  getSearchConnection,
  selectSearchEngine,
} from "@/services/searchConnection";
import { ExternalLink } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

const CLEVERSEE_KEY_URL = "https://ipaas.console.aliyun.com/api-key";

type Slot = NonNullable<SearchConnectionResponse["cleversee"]>;

/**
 * 设置 · 通用 · 联网搜索。
 *
 * 平台搜索始终在。开析和 SearXNG 各一个服务位：连接先验证，通过才保存并选用。
 */
export function SearchConnectionSection() {
  const [view, setView] = useState<SearchConnectionResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState<OwnSearchEngine | null>(null);
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [pendingDisconnect, setPendingDisconnect] =
    useState<OwnSearchEngine | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSearchConnection()
      .then((next) => {
        if (!cancelled) setView(next);
      })
      .catch((err: unknown) => {
        if (!cancelled) notifyError(err, "读取联网搜索设置失败");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (view == null) return null;

  const choose = (engine: SearchEngine): void => {
    if (busy || engine === view.selected) return;
    setBusy(true);
    selectSearchEngine(engine)
      .then(setView)
      .catch((err: unknown) => notifyError(err, "切换搜索失败"))
      .finally(() => setBusy(false));
  };

  const openForm = (engine: OwnSearchEngine): void => {
    setForm(engine);
    setApiKey("");
    setFormError(null);
    setBaseUrl(engine === "searxng" ? (view.searxng?.base_url ?? "") : "");
  };

  const closeForm = (): void => {
    setForm(null);
    setApiKey("");
    setBaseUrl("");
    setFormError(null);
  };

  const submit = (event: FormEvent): void => {
    event.preventDefault();
    if (busy || form == null) return;
    if (form === "cleversee" && !apiKey.trim()) {
      setFormError("开析需要 API Key");
      return;
    }
    if (form === "searxng" && !baseUrl.trim()) {
      setFormError("请填写 SearXNG 根地址");
      return;
    }
    setBusy(true);
    setFormError(null);
    const request =
      form === "cleversee"
        ? connectCleverSee(apiKey.trim())
        : connectSearxng(baseUrl.trim(), apiKey.trim());
    request
      .then((next) => {
        setView(next);
        closeForm();
      })
      .catch((err: unknown) => setFormError(connectionError(err)))
      .finally(() => setBusy(false));
  };

  const quota = view.quota;
  const platformDescription = `今日 ${countText(quota.daily_used, quota.daily_limit)} · 本月 ${countText(quota.monthly_used, quota.monthly_limit)}`;

  return (
    <SettingsSection title="联网搜索" contentClassName="space-y-3">
      <SettingRow
        variant="select"
        selected={view.selected === "platform"}
        disabled={busy}
        onClick={() => choose("platform")}
        label="平台搜索"
        description={platformDescription}
      />
      <EngineSlot
        engine="cleversee"
        label="开析"
        slot={view.cleversee ?? null}
        selected={view.selected === "cleversee"}
        busy={busy}
        formOpen={form === "cleversee"}
        onSelect={() => choose("cleversee")}
        onConnect={() => openForm("cleversee")}
        onDisconnect={() => setPendingDisconnect("cleversee")}
      />
      {form === "cleversee" ? (
        <ConnectForm
          engine="cleversee"
          apiKey={apiKey}
          baseUrl={baseUrl}
          error={formError}
          busy={busy}
          onApiKey={setApiKey}
          onBaseUrl={setBaseUrl}
          onSubmit={submit}
          onCancel={closeForm}
        />
      ) : null}
      <EngineSlot
        engine="searxng"
        label="SearXNG"
        slot={view.searxng ?? null}
        selected={view.selected === "searxng"}
        busy={busy}
        formOpen={form === "searxng"}
        onSelect={() => choose("searxng")}
        onConnect={() => openForm("searxng")}
        onDisconnect={() => setPendingDisconnect("searxng")}
      />
      {form === "searxng" ? (
        <ConnectForm
          engine="searxng"
          apiKey={apiKey}
          baseUrl={baseUrl}
          error={formError}
          busy={busy}
          onApiKey={setApiKey}
          onBaseUrl={setBaseUrl}
          onSubmit={submit}
          onCancel={closeForm}
        />
      ) : null}
      <ConfirmDialog
        open={pendingDisconnect != null}
        onOpenChange={(open) => {
          if (!open) setPendingDisconnect(null);
        }}
        title="断开这个搜索服务？"
        description={
          pendingDisconnect != null && view.selected === pendingDisconnect
            ? "断开后，联网搜索改回平台搜索。"
            : "当前正在使用的搜索不变。"
        }
        confirmLabel="断开"
        tone="danger"
        busy={busy}
        onConfirm={() => {
          if (pendingDisconnect == null) return;
          const engine = pendingDisconnect;
          setBusy(true);
          disconnectSearchEngine(engine)
            .then((next) => {
              setPendingDisconnect(null);
              if (form === engine) closeForm();
              setView(next);
            })
            .catch((err: unknown) => notifyError(err, "断开搜索服务失败"))
            .finally(() => setBusy(false));
        }}
      />
    </SettingsSection>
  );
}

function EngineSlot({
  engine,
  label,
  slot,
  selected,
  busy,
  formOpen,
  onSelect,
  onConnect,
  onDisconnect,
}: {
  engine: OwnSearchEngine;
  label: string;
  slot: Slot | null;
  selected: boolean;
  busy: boolean;
  formOpen: boolean;
  onSelect: () => void;
  onConnect: () => void;
  onDisconnect: () => void;
}) {
  if (slot == null) {
    return (
      <SettingRow
        label={label}
        description="未连接"
        control={
          formOpen ? undefined : (
            <Button
              type="button"
              size="sm"
              disabled={busy}
              aria-label={slotAction("连接", label)}
              onClick={onConnect}
            >
              连接
            </Button>
          )
        }
      />
    );
  }

  return (
    <div className="space-y-2">
      <SettingRow
        variant="select"
        selected={selected}
        disabled={busy}
        onClick={onSelect}
        label={label}
        description={slotDescription(engine, slot)}
      />
      {formOpen ? null : (
        <div className="flex gap-2 pl-1">
          <Button
            type="button"
            variant="neutral"
            size="sm"
            disabled={busy}
            aria-label={slotAction("更换", label, "密钥")}
            onClick={onConnect}
          >
            更换密钥
          </Button>
          <Button
            type="button"
            variant="neutral"
            size="sm"
            disabled={busy}
            aria-label={slotAction("断开", label)}
            onClick={onDisconnect}
          >
            断开
          </Button>
        </div>
      )}
    </div>
  );
}

function ConnectForm({
  engine,
  apiKey,
  baseUrl,
  error,
  busy,
  onApiKey,
  onBaseUrl,
  onSubmit,
  onCancel,
}: {
  engine: OwnSearchEngine;
  apiKey: string;
  baseUrl: string;
  error: string | null;
  busy: boolean;
  onApiKey: (value: string) => void;
  onBaseUrl: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  onCancel: () => void;
}) {
  const label = engine === "cleversee" ? "开析" : "SearXNG";
  return (
    <form onSubmit={onSubmit} className="space-y-3">
      {engine === "searxng" ? (
        <SettingField
          label="地址"
          htmlFor="searxng-base-url"
          hint="自己的 SearXNG 根地址，需为 http 或 https。"
          error={error}
        >
          <Input
            id="searxng-base-url"
            value={baseUrl}
            disabled={busy}
            placeholder="https://"
            onChange={(event) => onBaseUrl(event.target.value)}
          />
        </SettingField>
      ) : null}
      <SettingField
        label="API Key"
        htmlFor={`${engine}-api-key`}
        hint={
          engine === "cleversee" ? (
            <>
              到开析控制台创建 API Key，不是阿里云 AccessKey。{" "}
              <a
                href={CLEVERSEE_KEY_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-primary hover:underline"
              >
                打开控制台
                <ExternalLink size={14} />
              </a>
            </>
          ) : (
            "可选。不填则按公开实例连接。"
          )
        }
        error={engine === "cleversee" ? error : null}
      >
        <Input
          id={`${engine}-api-key`}
          type="password"
          autoComplete="off"
          value={apiKey}
          disabled={busy}
          onChange={(event) => onApiKey(event.target.value)}
        />
      </SettingField>
      <div className="flex gap-2">
        <Button
          type="submit"
          size="sm"
          disabled={busy}
          aria-label={slotAction("连接", label)}
        >
          连接
        </Button>
        <Button
          type="button"
          variant="neutral"
          size="sm"
          disabled={busy}
          onClick={onCancel}
        >
          取消
        </Button>
      </div>
    </form>
  );
}

function slotAction(verb: string, label: string, tail = ""): string {
  if (label === "SearXNG") {
    return tail ? `${verb} SearXNG ${tail}` : `${verb} SearXNG`;
  }
  return `${verb}${label}${tail}`;
}

function slotDescription(engine: OwnSearchEngine, slot: Slot): string {
  if (slot.status === "needs_reconnect") {
    return slot.message ? `需要重新连接 · ${slot.message}` : "需要重新连接";
  }
  const parts = ["已连接"];
  if (engine === "searxng" && slot.base_url) {
    parts.push(hostOf(slot.base_url));
  }
  if (slot.masked_key) parts.push(slot.masked_key);
  return parts.join(" · ");
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function countText(used: number, limit: number): string {
  return limit <= 0 ? `${used}/不限` : `${used}/${limit}`;
}

function connectionError(err: unknown): string {
  if (err instanceof ApiError && err.serverMessage?.trim()) {
    return err.serverMessage;
  }
  return "连接失败，请重试";
}
