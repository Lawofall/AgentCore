import {
  SettingRow,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { IconButton, PageHeader, SegmentedControl } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import {
  FONT_SIZE_ITEMS,
  ZOOM_MAX,
  ZOOM_MIN,
  formatZoomPercent,
} from "@/lib/appearance";
import { type Theme, resolveDark } from "@/lib/theme";
import { useNarrowLayout } from "@/lib/useNarrowLayout";
import { cn } from "@/lib/utils";
import { useUIStore } from "@/stores/ui";
import { type LucideIcon, Minus, Monitor, Moon, Plus, Sun } from "lucide-react";

interface ThemeOption {
  value: Theme;
  label: string;
  description: string;
  icon: LucideIcon;
}

const SEND_MODE_ITEMS = [
  { value: "enter", label: "Enter 发送" },
  { value: "newline", label: "Enter 换行" },
] as const;

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
 * 通用设置（/more/general）— 外观、发送方式、系统通知、开发者模式。
 *
 * 主题写入 `useUIStore.theme`（本机 uiStorage），由 `lib/theme.ts` 收口。
 * 字号与整窗缩放同店、由 `lib/appearance.ts` 收口（字号只乘四级 token；缩放整窗）。
 * 窄屏露出字号与发送方式，藏主题、缩放、系统通知、开发者模式。联网搜索在 `/more/search`。
 *
 * 旧路径 `/more/appearance` 在 router 里重定向到这里。
 */
export function GeneralSettings() {
  const isNarrow = useNarrowLayout();
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const fontSize = useUIStore((s) => s.fontSize);
  const setFontSize = useUIStore((s) => s.setFontSize);
  const zoom = useUIStore((s) => s.zoom);
  const nudgeZoom = useUIStore((s) => s.nudgeZoom);
  const resetZoom = useUIStore((s) => s.resetZoom);
  const sendOnEnter = useUIStore((s) => s.sendOnEnter);
  const setSendOnEnter = useUIStore((s) => s.setSendOnEnter);
  const osNotifications = useUIStore((s) => s.osNotifications);
  const setOsNotifications = useUIStore((s) => s.setOsNotifications);
  const developerMode = useUIStore((s) => s.developerMode);
  const setDeveloperMode = useUIStore((s) => s.setDeveloperMode);

  return (
    <div>
      <PageHeader title="通用" />

      <SettingsStack>
        <SettingsSection title="外观" contentClassName="space-y-3">
          {!isNarrow && (
            <div className="space-y-2">
              {THEME_OPTIONS.map((option) => (
                <ThemeRow
                  key={option.value}
                  option={option}
                  selected={theme === option.value}
                  onSelect={() => setTheme(option.value)}
                />
              ))}
            </div>
          )}

          <div>
            {!isNarrow && (
              <p className="mb-2 text-sm font-medium text-foreground">字号</p>
            )}
            <SegmentedControl
              aria-label="字号"
              value={fontSize}
              onChange={setFontSize}
              items={FONT_SIZE_ITEMS}
              fit
            />
          </div>

          {!isNarrow && (
            <SettingRow
              label="界面缩放"
              control={
                <div className="flex items-center gap-1">
                  <IconButton
                    size="sm"
                    aria-label="缩小界面"
                    disabled={zoom <= ZOOM_MIN}
                    onClick={() => nudgeZoom(-1)}
                  >
                    <Minus size={16} />
                  </IconButton>
                  <button
                    type="button"
                    className="min-w-12 rounded-lg px-1 text-center text-sm tabular-nums text-foreground hover:bg-accent disabled:hover:bg-transparent"
                    aria-label="恢复默认缩放"
                    disabled={zoom === 1}
                    onClick={resetZoom}
                  >
                    {formatZoomPercent(zoom)}
                  </button>
                  <IconButton
                    size="sm"
                    aria-label="放大界面"
                    disabled={zoom >= ZOOM_MAX}
                    onClick={() => nudgeZoom(1)}
                  >
                    <Plus size={16} />
                  </IconButton>
                </div>
              }
            />
          )}
        </SettingsSection>

        <SettingsSection title="发送">
          <SegmentedControl
            aria-label="发送方式"
            value={sendOnEnter ? "enter" : "newline"}
            onChange={(v) => setSendOnEnter(v === "enter")}
            items={SEND_MODE_ITEMS}
            fit
          />
        </SettingsSection>

        {!isNarrow && (
          <SettingsSection title="通知">
            <SettingRow
              label="系统通知"
              description="关了只停系统横幅，侧栏灯和应用内提示还在。"
              control={
                <Switch
                  checked={osNotifications}
                  label="系统通知"
                  onCheckedChange={setOsNotifications}
                />
              }
            />
          </SettingsSection>
        )}

        {!isNarrow && (
          <SettingsSection title="开发者模式">
            <SettingRow
              label="查看这一回合"
              description="打开后，回复底栏可以打开这一回合的记录。"
              control={
                <Switch
                  checked={developerMode}
                  label="开发者模式"
                  onCheckedChange={setDeveloperMode}
                />
              }
            />
          </SettingsSection>
        )}
      </SettingsStack>
    </div>
  );
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
