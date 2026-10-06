import { Badge } from "@/components/ui/badge";
import { Input, fieldSurfaceClass } from "@/components/ui/input";
import { SearchField } from "@/components/ui/search-field";
import {
  type DefaultModelOption,
  type DefaultProviderGroup,
  PLATFORM_POINTER_ID,
  decodePointer,
  encodePointer,
  isPlatformGroupId,
  modelInChannelCatalog,
  unavailableReasonCopy,
} from "@/lib/llmDefaults";
import { cn } from "@/lib/utils";
import type { ModelPriceCard } from "@/services/models";
import { Check, ChevronDown, PencilLine } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";

/**
 * 模型组合槽位选择器 — 渠道芯片 + 当前渠模型列表。
 *
 * 对齐货架筛选芯片（先锁一类再看叶子），不是两个并列字段：点一行仍只钉
 * `@platform/{id}` / `@byok/{provider_id}/{id}`。多家渠道时芯片互斥；只一家则
 * 不画芯片。筛选默认只打当前渠；当前渠无命中才把其他渠结果补在「当前渠道无匹配」下。
 * BYOK 渠末尾「自定义 model id…」；平台档无自定义入口。触发器副行前置渠道名。
 */

function formatContextLength(n: number | null | undefined): string | null {
  if (n == null || !Number.isFinite(n) || n <= 0) return null;
  if (n >= 1_000_000)
    return `${(n / 1_000_000).toFixed(n % 1_000_000 === 0 ? 0 : 1)}M`;
  if (n >= 1_000) return `${Math.round(n / 1_000)}K`;
  return String(n);
}

function unitPriceSymbol(currency: string | null | undefined): string {
  const code = (currency || "CNY").toUpperCase();
  if (code === "CNY") return "¥";
  if (code === "USD") return "$";
  return `${code} `;
}

function formatPrice(price: ModelPriceCard | null | undefined): string | null {
  if (!price) return null;
  const inn = price.cache_miss?.trim() || null;
  const out = price.output?.trim() || null;
  if (!inn && !out) return null;
  const sym = unitPriceSymbol(price.currency);
  if (inn && out) return `${sym}${inn} / ${sym}${out}`;
  if (inn) return `${sym}${inn} in`;
  return `${sym}${out} out`;
}

function capabilityBits(caps: string[] | undefined): string | null {
  if (!caps?.length) return null;
  const order = ["tools", "vision", "reasoning"] as const;
  const hit = order.filter((c) => caps.includes(c));
  return hit.length ? hit.join("·") : null;
}

/** 副行文案：平台带徽章时优先展示原始 model id。 */
export function modelOptionSecondary(opt: DefaultModelOption): string {
  if (opt.badge && opt.model && opt.label !== opt.model) {
    return opt.model;
  }
  const parts: string[] = [];
  if (opt.vendor) parts.push(opt.vendor);
  const ctx = formatContextLength(opt.contextLength);
  if (ctx) parts.push(ctx);
  const caps = capabilityBits(opt.capabilities);
  if (caps) parts.push(caps);
  const price = formatPrice(opt.price);
  if (price) parts.push(price);
  if (opt.custom) parts.push("自定义");
  return parts.join(" · ");
}

/**
 * 触发器副行：前置渠道名；渠道名与 vendor 相同时不重复。
 */
export function triggerSecondaryLine(
  providerLabel: string | undefined,
  opt: DefaultModelOption | undefined,
  custom: boolean,
): string | undefined {
  const channel = providerLabel?.trim() || "";
  if (custom) {
    return channel ? `${channel} · 自定义 model id` : "自定义 model id";
  }
  if (!opt) {
    return channel || undefined;
  }
  const bits = modelOptionSecondary(opt);
  if (!channel) return bits || undefined;
  const vendor = opt.vendor?.trim() || "";
  if (vendor && vendor === channel) {
    return bits || undefined;
  }
  // 平台徽章行是 raw model id，仍前置渠道名。
  if (bits) return `${channel} · ${bits}`;
  return channel;
}

function providerIdFromPointer(value: string): string | null {
  const decoded = value ? decodePointer(value) : null;
  if (!decoded) return null;
  if (decoded.origin === "platform" || !decoded.provider_id) {
    return PLATFORM_POINTER_ID;
  }
  return decoded.provider_id;
}

function findOption(
  group: DefaultProviderGroup | undefined,
  model: string,
): DefaultModelOption | undefined {
  return group?.models.find((m) => m.model === model);
}

function groupHeading(g: DefaultProviderGroup): string {
  return g.orphan ? `${g.providerLabel}（需改选）` : g.providerLabel;
}

function optionMatchesQuery(opt: DefaultModelOption, q: string): boolean {
  const reason = unavailableReasonCopy(opt.unavailableReason) ?? "";
  return (
    opt.label.toLowerCase().includes(q) ||
    opt.model.toLowerCase().includes(q) ||
    (opt.vendor ?? "").toLowerCase().includes(q) ||
    (opt.badge ?? "").toLowerCase().includes(q) ||
    reason.toLowerCase().includes(q)
  );
}

export type ChannelPickerSection = {
  group: DefaultProviderGroup;
  models: DefaultModelOption[];
  showCustom: boolean;
  /** 当前渠筛选无命中时，其他渠的补列。 */
  fallback: boolean;
};

/** 打开弹层时默认看哪条渠：跟现值；孤儿现值则落到第一家还能选的活渠。 */
export function defaultBrowseProviderId(
  groups: DefaultProviderGroup[],
  value: string,
): string {
  const fromValue = providerIdFromPointer(value);
  const current = fromValue
    ? groups.find((g) => g.providerId === fromValue)
    : undefined;
  if (current && !current.orphan) return current.providerId;
  const withSelectable = groups.find(
    (g) =>
      !g.orphan && g.models.some((m) => m.available !== false && !m.custom),
  );
  if (withSelectable) return withSelectable.providerId;
  const live = groups.find((g) => !g.orphan);
  return live?.providerId || groups[0]?.providerId || "";
}

export function buildChannelPickerSections(
  groups: DefaultProviderGroup[],
  browseProviderId: string,
  query: string,
): ChannelPickerSection[] {
  const scoped =
    groups.find((g) => g.providerId === browseProviderId) ?? groups[0];
  if (!scoped) return [];
  const q = query.trim().toLowerCase();
  const scopedModels = q
    ? scoped.models.filter((m) => optionMatchesQuery(m, q))
    : scoped.models;
  const labelHit = Boolean(q) && scoped.providerLabel.toLowerCase().includes(q);
  const scopedHit = scopedModels.length > 0 || labelHit;
  const showCustom =
    !isPlatformGroupId(scoped.providerId) &&
    !scoped.orphan &&
    (!q || scopedModels.length === 0);

  const sections: ChannelPickerSection[] = [
    {
      group: scoped,
      models: scopedModels,
      showCustom,
      fallback: false,
    },
  ];

  if (q && !scopedHit) {
    for (const g of groups) {
      if (g.providerId === scoped.providerId) continue;
      const models = g.models.filter((m) => optionMatchesQuery(m, q));
      if (models.length === 0) continue;
      sections.push({
        group: g,
        models,
        showCustom: false,
        fallback: true,
      });
    }
  }
  return sections;
}

export function ProfileModelSelect({
  id,
  groups,
  value,
  disabled,
  followLabel,
  labelledBy,
  describedBy,
  compactTrigger = false,
  onChange,
}: {
  id?: string;
  groups: DefaultProviderGroup[];
  value: string;
  disabled?: boolean;
  /** 可选槽：空值文案（跟随主模型 / 不配置）。 */
  followLabel?: string;
  /** 外部标签元素 id；与触发器自身并列，读屏念「标签 + 当前取值」。 */
  labelledBy?: string;
  /** 外部说明元素 id。 */
  describedBy?: string;
  /**
   * Closed trigger is one line (the name) at the same height as a native
   * select. Window, capabilities and price stay on the open rows.
   */
  compactTrigger?: boolean;
  onChange: (value: string) => void;
}) {
  const decoded = value ? decodePointer(value) : null;
  const [providerId, setProviderId] = useState(
    () => providerIdFromPointer(value) || groups[0]?.providerId || "",
  );
  const [customMode, setCustomMode] = useState(() => {
    const pid = providerIdFromPointer(value);
    const g = groups.find((x) => x.providerId === pid);
    const m = decoded?.model ?? "";
    if (!m || !g) return false;
    if (isPlatformGroupId(g.providerId)) return false;
    return !modelInChannelCatalog(g, m);
  });
  const [customText, setCustomText] = useState(() => decoded?.model ?? "");
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [browseProviderId, setBrowseProviderId] = useState(() =>
    defaultBrowseProviderId(groups, value),
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listId = useId();

  // 外部 value 变更（加载草稿 / 清空）时同步本地态。
  useEffect(() => {
    if (!value) {
      setCustomText("");
      setCustomMode(false);
      return;
    }
    const d = decodePointer(value);
    if (!d) return;
    const pid = providerIdFromPointer(value);
    if (pid) setProviderId(pid);
    const g = groups.find((x) => x.providerId === pid);
    const inCatalog = modelInChannelCatalog(g, d.model);
    if (pid && !isPlatformGroupId(pid) && !inCatalog) {
      setCustomMode(true);
      setCustomText((prev) => (prev.trim() === d.model ? prev : d.model));
    } else {
      setCustomMode(false);
      setCustomText(d.model);
    }
  }, [value, groups]);

  useEffect(() => {
    if (!open) return;
    // Popover opens → move focus into the filter so keyboard users can type / Tab to options.
    searchRef.current?.focus();
    const onDoc = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const selectedGroup = groups.find((g) => g.providerId === providerId);
  const allowCustom = Boolean(
    selectedGroup && !isPlatformGroupId(selectedGroup.providerId),
  );

  const emit = (pid: string, model: string) => {
    const trimmed = model.trim();
    if (pid && trimmed) onChange(encodePointer(pid, trimmed));
    else onChange("");
  };

  const pickCatalog = (pid: string, opt: DefaultModelOption) => {
    setProviderId(pid);
    setCustomMode(false);
    setCustomText(opt.model);
    setOpen(false);
    setQuery("");
    emit(pid, opt.model);
  };

  const enterCustom = (pid: string) => {
    if (isPlatformGroupId(pid)) return;
    setProviderId(pid);
    setCustomMode(true);
    setOpen(false);
    setQuery("");
    // 不在此 emit：若种子值恰在该渠道目录内，会被同步 effect 判为目录项而踢出自定义态。
    // 换渠道时旧 model id 对新渠道无意义，清掉；同渠道则留作种子。
    const sameChannel = pid === providerIdFromPointer(value);
    if (!sameChannel || !decoded?.model) setCustomText("");
  };

  const openPicker = () => {
    setQuery("");
    setBrowseProviderId(defaultBrowseProviderId(groups, value));
    setOpen(true);
  };

  const sections = useMemo(
    () => buildChannelPickerSections(groups, browseProviderId, query),
    [groups, browseProviderId, query],
  );
  const showChannelChips = groups.length > 1;
  const hasPickerRows = sections.some(
    (s) => s.models.length > 0 || s.showCustom,
  );

  const triggerLabel = (): {
    title: string;
    badge?: string | null;
    sub?: string;
    unavailable?: boolean;
  } => {
    if (!decoded?.model) {
      return {
        title: followLabel !== undefined ? followLabel : "选择模型",
      };
    }
    const opt =
      findOption(selectedGroup, decoded.model) ??
      findOption(
        groups.find(
          (g) => g.providerId === (providerIdFromPointer(value) || ""),
        ),
        decoded.model,
      );
    if (customMode) {
      return {
        title: decoded.model,
        sub: triggerSecondaryLine(
          selectedGroup?.providerLabel,
          undefined,
          true,
        ),
      };
    }
    if (opt) {
      const reason =
        opt.available === false
          ? unavailableReasonCopy(opt.unavailableReason)
          : null;
      return {
        title: opt.label,
        badge: opt.badge,
        sub:
          reason ||
          triggerSecondaryLine(selectedGroup?.providerLabel, opt, false),
        unavailable: Boolean(reason),
      };
    }
    return {
      title: decoded.model,
      sub: triggerSecondaryLine(selectedGroup?.providerLabel, undefined, false),
    };
  };

  const trigger = triggerLabel();
  const triggerSub =
    trigger.sub && (!compactTrigger || trigger.unavailable)
      ? trigger.sub
      : undefined;
  const channelChipLabel = selectedGroup
    ? selectedGroup.orphan
      ? `${selectedGroup.providerLabel}（需改选）`
      : selectedGroup.providerLabel
    : null;

  return (
    <div ref={rootRef} className="relative">
      {customMode && allowCustom ? (
        <div>
          <div className="flex items-center gap-2">
            <Input
              id={id}
              value={customText}
              disabled={disabled}
              aria-label="自定义 model id"
              aria-describedby={describedBy}
              placeholder="model id，如 ep-xxxx"
              onChange={(e) => {
                const next = e.target.value;
                setCustomText(next);
                emit(providerId, next);
              }}
              className="min-w-0 flex-1"
            />
            {channelChipLabel ? (
              <Badge tone="muted">{channelChipLabel}</Badge>
            ) : null}
            <Badge tone="muted">自定义</Badge>
          </div>
          <button
            type="button"
            disabled={disabled}
            className="mt-1.5 text-xs text-primary underline-offset-2 hover:underline disabled:opacity-60"
            onClick={() => {
              setCustomMode(false);
              openPicker();
            }}
          >
            从目录选择
          </button>
        </div>
      ) : (
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-labelledby={
            labelledBy && id ? `${labelledBy} ${id}` : labelledBy
          }
          aria-describedby={describedBy}
          onClick={() => {
            if (disabled) return;
            if (open) setOpen(false);
            else openPicker();
          }}
          className={cn(
            compactTrigger
              ? cn(
                  fieldSurfaceClass,
                  "flex w-full items-center gap-2 px-2.5 text-left",
                  triggerSub ? "h-auto min-h-8 py-1" : "h-8",
                )
              : cn(
                  "mt-1.5 flex w-full items-center gap-2 rounded-lg border border-input bg-background px-2.5 text-left text-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60",
                  triggerSub ? "min-h-9 py-1.5" : "h-9",
                ),
            !decoded?.model && "text-muted-foreground",
          )}
        >
          <span className="min-w-0 flex-1">
            <span className="flex flex-wrap items-center gap-1.5">
              <span
                className={cn(
                  "truncate",
                  decoded?.model ? "text-foreground" : "text-muted-foreground",
                )}
              >
                {trigger.title}
              </span>
              {trigger.badge ? (
                <Badge tone="primary">{trigger.badge}</Badge>
              ) : null}
            </span>
            {triggerSub ? (
              <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                {triggerSub}
              </span>
            ) : null}
          </span>
          <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
        </button>
      )}

      {open && !customMode && (
        <div className="absolute z-50 mt-1 flex max-h-96 w-full flex-col overflow-hidden rounded-lg border border-border bg-popover shadow-md">
          <div className="border-b border-border p-1.5">
            <SearchField
              ref={searchRef}
              size="sm"
              variant="plain"
              value={query}
              onValueChange={setQuery}
              placeholder="筛选模型"
              aria-label="筛选模型"
            />
          </div>
          {showChannelChips ? (
            <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 border-b border-border p-0 px-2.5 py-1.5">
              <legend className="sr-only">渠道</legend>
              {groups.map((g) => {
                const pressed = browseProviderId === g.providerId;
                return (
                  <Badge
                    key={g.providerId}
                    as="button"
                    pill
                    data-channel-id={g.providerId}
                    aria-pressed={pressed}
                    tone={pressed ? "primary" : "muted"}
                    onClick={() => setBrowseProviderId(g.providerId)}
                  >
                    {groupHeading(g)}
                  </Badge>
                );
              })}
            </fieldset>
          ) : null}
          <div
            id={listId}
            // biome-ignore lint/a11y/useSemanticElements: custom searchable catalog; native <select> cannot host grouped options.
            role="listbox"
            tabIndex={-1}
            className="max-h-56 overflow-y-auto py-1"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setOpen(false);
              }
            }}
          >
            {!hasPickerRows ? (
              <p className="px-2.5 py-2 text-xs text-muted-foreground">
                无匹配模型
              </p>
            ) : (
              sections.map((s) => {
                const firstFallback =
                  s.fallback && s === sections.find((x) => x.fallback);
                return (
                  <div
                    key={`${s.fallback ? "fb:" : ""}${s.group.providerId}`}
                    data-provider-group={s.group.providerId}
                  >
                    {firstFallback ? (
                      <div className="px-2.5 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">
                        当前渠道无匹配
                      </div>
                    ) : null}
                    {s.fallback ? (
                      <div className="px-2.5 pt-1.5 pb-0.5 text-xs font-medium text-muted-foreground">
                        {groupHeading(s.group)}
                      </div>
                    ) : null}
                    {s.models.map((m) => {
                      const selected =
                        providerId === s.group.providerId &&
                        decoded?.model === m.model &&
                        !customMode;
                      return (
                        <ModelOptionRow
                          key={`${s.group.providerId}:${m.model}`}
                          option={m}
                          selected={selected}
                          onPick={() => pickCatalog(s.group.providerId, m)}
                        />
                      );
                    })}
                    {s.showCustom ? (
                      <button
                        type="button"
                        // biome-ignore lint/a11y/useSemanticElements: listbox option must stay a button for Enter/click; native <option> is not focusable in this popup.
                        role="option"
                        aria-selected={false}
                        className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm text-muted-foreground hover:bg-accent/50"
                        onClick={() => enterCustom(s.group.providerId)}
                      >
                        <PencilLine size={14} className="shrink-0" />
                        自定义 model id…
                      </button>
                    ) : null}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}

      {selectedGroup?.orphan ? (
        <p className="mt-1 text-xs text-primary">
          原服务商已移除，请改选其他渠道与模型后再保存。
        </p>
      ) : null}
    </div>
  );
}

function ModelOptionRow({
  option,
  selected,
  onPick,
}: {
  option: DefaultModelOption;
  selected: boolean;
  onPick: () => void;
}) {
  const unavailable = option.available === false;
  const reason = unavailable
    ? unavailableReasonCopy(option.unavailableReason)
    : null;
  const secondary = reason ?? modelOptionSecondary(option);
  return (
    <button
      type="button"
      // biome-ignore lint/a11y/useSemanticElements: listbox option must stay a button for Enter/click; native <option> is not focusable in this popup.
      role="option"
      aria-selected={selected}
      aria-disabled={unavailable || undefined}
      onClick={() => {
        if (unavailable) return;
        onPick();
      }}
      className={cn(
        "flex w-full items-start gap-2 px-2.5 py-1.5 text-left",
        unavailable
          ? "cursor-not-allowed opacity-60"
          : selected
            ? "bg-primary/10"
            : "hover:bg-accent/50",
      )}
    >
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-1.5">
          <span
            className={cn(
              "truncate text-sm",
              unavailable ? "text-muted-foreground" : "text-foreground",
            )}
          >
            {option.label}
          </span>
          {option.badge ? <Badge tone="primary">{option.badge}</Badge> : null}
          {option.custom ? <Badge tone="muted">自定义</Badge> : null}
        </span>
        {secondary ? (
          <span className="mt-0.5 block truncate text-xs text-muted-foreground">
            {secondary}
          </span>
        ) : null}
      </span>
      {selected && !unavailable ? (
        <Check size={14} className="mt-0.5 shrink-0 text-primary" />
      ) : null}
    </button>
  );
}

/**
 * 是否仍可继续选模型：目录非自定义项，或存在可用 BYOK 渠道（可手填）。
 * 仅孤儿自定义折叠项不算「可继续选」。
 */
export function canChooseFromGroups(groups: DefaultProviderGroup[]): boolean {
  const hasCatalog = groups.some((g) =>
    g.models.some((m) => !m.custom && m.available !== false),
  );
  const hasByok = groups.some(
    (g) => !isPlatformGroupId(g.providerId) && !g.orphan,
  );
  return hasCatalog || hasByok;
}
