import {
  SettingField,
  SettingRow,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import {
  Button,
  ConfirmDialog,
  Input,
  PageHeader,
  Select,
} from "@/components/ui";
import { type Theme, resolveDark } from "@/lib/theme";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  type SearchProvider,
  type SearchProviderProtocol,
  type SearchProvidersResponse,
  createSearchProvider,
  deleteSearchProvider,
  getSearchProviders,
  selectSearchProvider,
  testSearchProvider,
} from "@/services/searchProviders";
import { useUIStore } from "@/stores/ui";
import { type LucideIcon, Monitor, Moon, Sun } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

interface ThemeOption {
  value: Theme;
  label: string;
  description: string;
  icon: LucideIcon;
}

const THEME_OPTIONS: ThemeOption[] = [
  {
    value: "light",
    label: "浅色",
    description: "始终使用浅色界面。",
    icon: Sun,
  },
  {
    value: "dark",
    label: "深色",
    description: "始终使用深色界面。",
    icon: Moon,
  },
  {
    value: "system",
    label: "跟随系统",
    description: "随操作系统的外观自动切换。",
    icon: Monitor,
  },
];

/**
 * 通用设置（/more/general）— 主题与联网搜索。
 *
 * 主题写入共享的 `useUIStore.theme`（持久化到 localStorage），应用由 `lib/theme.ts`
 * 统一收口（AppShell 的 `useApplyTheme` 切 root `.dark` 类、`系统`档随 OS 跟随），
 * 与命令面板的「切换主题」命令同一条链路——这里只是它的可视化入口。
 *
 * 旧路径 `/more/appearance` 在 router 里重定向到这里。
 */
export function GeneralSettings() {
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);

  return (
    <div>
      <PageHeader title="通用" />

      <SettingsStack>
        <SettingsSection title="主题" contentClassName="space-y-2">
          {THEME_OPTIONS.map((option) => (
            <ThemeRow
              key={option.value}
              option={option}
              selected={theme === option.value}
              onSelect={() => setTheme(option.value)}
            />
          ))}
        </SettingsSection>

        <SearchEngineSection />
      </SettingsStack>
    </div>
  );
}

function countText(used: number, limit: number): string {
  return limit <= 0 ? `${used}/不限` : `${used}/${limit}`;
}

function SearchEngineSection() {
  const [view, setView] = useState<SearchProvidersResponse | null>(null);
  const [saving, setSaving] = useState(false);
  const [protocol, setProtocol] = useState<SearchProviderProtocol>("cleversee");
  const [label, setLabel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [pendingDelete, setPendingDelete] = useState<SearchProvider | null>(
    null,
  );

  const reload = (): void => {
    getSearchProviders()
      .then(setView)
      .catch((err: unknown) => notifyError(err, "读取联网搜索设置失败"));
  };

  useEffect(() => {
    let cancelled = false;
    getSearchProviders()
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

  const choose = (providerId: string | null): void => {
    if (saving || providerId === view.selected_provider_id) return;
    setSaving(true);
    selectSearchProvider(providerId)
      .then(setView)
      .catch((err: unknown) => notifyError(err, "切换搜索失败"))
      .finally(() => setSaving(false));
  };

  const add = (event: FormEvent): void => {
    event.preventDefault();
    if (saving) return;
    if (protocol === "cleversee" && !apiKey.trim()) {
      notifyError(new Error("开析需要 API key"), "开析需要 API key");
      return;
    }
    setSaving(true);
    createSearchProvider({
      protocol,
      label: label.trim(),
      base_url: baseUrl.trim(),
      api_key: apiKey.trim(),
    })
      .then(() => {
        setLabel("");
        setBaseUrl("");
        setApiKey("");
        reload();
      })
      .catch((err: unknown) => notifyError(err, "添加搜索服务失败"))
      .finally(() => setSaving(false));
  };

  const quota = view.quota;
  const platformDescription = `今日 ${countText(quota.daily_used, quota.daily_limit)} · 本月 ${countText(quota.monthly_used, quota.monthly_limit)}`;

  return (
    <SettingsSection title="联网搜索" contentClassName="space-y-3">
      <SettingRow
        variant="select"
        selected={view.selected_provider_id == null}
        disabled={saving}
        onClick={() => choose(null)}
        label="平台搜索"
        description={platformDescription}
      />
      {view.providers.map((provider) => (
        <div key={provider.id} className="space-y-2">
          <SettingRow
            variant="select"
            selected={view.selected_provider_id === provider.id}
            disabled={saving}
            onClick={() => choose(provider.id)}
            label={provider.label}
            description={providerDescription(provider)}
          />
          <div className="flex gap-2 pl-1">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={saving}
              onClick={() => {
                setSaving(true);
                testSearchProvider(provider.id)
                  .then((next) => {
                    setView((current) =>
                      current
                        ? {
                            ...current,
                            providers: current.providers.map((row) =>
                              row.id === next.id ? { ...row, ...next } : row,
                            ),
                          }
                        : current,
                    );
                  })
                  .catch((err: unknown) => notifyError(err, "测试搜索服务失败"))
                  .finally(() => setSaving(false));
              }}
            >
              测试
            </Button>
            <Button
              type="button"
              variant="neutral"
              size="sm"
              disabled={saving}
              onClick={() => setPendingDelete(provider)}
            >
              删除
            </Button>
          </div>
        </div>
      ))}

      <form onSubmit={add} className="space-y-3 pt-2">
        <SettingField label="协议" htmlFor="search-protocol">
          <Select
            id="search-protocol"
            value={protocol}
            disabled={saving}
            onChange={(event) =>
              setProtocol(event.target.value as SearchProviderProtocol)
            }
          >
            <option value="cleversee">开析</option>
            <option value="searxng">SearXNG</option>
          </Select>
        </SettingField>
        <SettingField label="名称" htmlFor="search-label">
          <Input
            id="search-label"
            value={label}
            disabled={saving}
            placeholder={protocol === "cleversee" ? "开析" : "SearXNG"}
            onChange={(event) => setLabel(event.target.value)}
          />
        </SettingField>
        <SettingField
          label="地址"
          htmlFor="search-base-url"
          hint={
            protocol === "cleversee"
              ? "留空则使用开析默认地址。"
              : "自己的 SearXNG 根地址，需为 http 或 https。"
          }
        >
          <Input
            id="search-base-url"
            value={baseUrl}
            disabled={saving}
            placeholder="https://"
            onChange={(event) => setBaseUrl(event.target.value)}
          />
        </SettingField>
        <SettingField label="API key" htmlFor="search-api-key">
          <Input
            id="search-api-key"
            type="password"
            autoComplete="off"
            value={apiKey}
            disabled={saving}
            onChange={(event) => setApiKey(event.target.value)}
          />
        </SettingField>
        <Button type="submit" size="sm" disabled={saving}>
          添加
        </Button>
      </form>

      <ConfirmDialog
        open={pendingDelete != null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
        title="删除这个搜索服务？"
        description="若当前正在使用它，会改回平台搜索。"
        confirmLabel="删除"
        tone="danger"
        busy={saving}
        onConfirm={() => {
          if (pendingDelete == null) return;
          setSaving(true);
          deleteSearchProvider(pendingDelete.id)
            .then(() => {
              setPendingDelete(null);
              reload();
            })
            .catch((err: unknown) => notifyError(err, "删除搜索服务失败"))
            .finally(() => setSaving(false));
        }}
      />
    </SettingsSection>
  );
}

function providerDescription(provider: SearchProvider): string {
  const kind = provider.protocol === "cleversee" ? "开析" : "SearXNG";
  const key = provider.masked_key ? ` · ${provider.masked_key}` : "";
  const status =
    provider.status === "active"
      ? "已连通"
      : provider.status === "error"
        ? "未连通"
        : "未测试";
  return `${kind} · ${status}${key}`;
}

/** One selectable theme row: icon badge + label/description. The 跟随系统 row
 * also shows what it currently resolves to. */
function ThemeRow({
  option,
  selected,
  onSelect,
}: {
  option: ThemeOption;
  selected: boolean;
  onSelect: () => void;
}) {
  const Icon = option.icon;
  const resolvedHint =
    option.value === "system"
      ? ` · 当前解析为「${resolveDark("system") ? "深色" : "浅色"}」`
      : "";

  return (
    <SettingRow
      variant="select"
      selected={selected}
      onClick={onSelect}
      label={option.label}
      description={`${option.description}${resolvedHint}`}
      leading={
        <span
          className={cn(
            "flex size-8 shrink-0 items-center justify-center rounded-lg",
            selected
              ? "bg-primary/10 text-primary"
              : "bg-muted text-muted-foreground",
          )}
        >
          <Icon size={16} />
        </span>
      }
    />
  );
}
