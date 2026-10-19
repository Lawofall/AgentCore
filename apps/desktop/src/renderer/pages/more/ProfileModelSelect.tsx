import { Badge } from "@/components/ui/badge";
import { Input, fieldSurfaceClass } from "@/components/ui/input";
import { SearchField } from "@/components/ui/search-field";
import {
  type DefaultModelOption,
  type DefaultProviderGroup,
  PLATFORM_ORIGIN_BADGE,
  PLATFORM_POINTER_ID,
  decodePointer,
  encodePointer,
  isPlatformGroupId,
  modelInChannelCatalog,
  unavailableReasonCopy,
} from "@/lib/llmDefaults";
import {
  channelHasListedModels,
  emptyByokGroups,
  flattenPickerRows,
  isHandFilledOption,
  isModelPickerFloatTarget,
  revealSelectedPickerOption,
} from "@/lib/modelPicker";
import { cn } from "@/lib/utils";
import { Check, ChevronDown } from "lucide-react";
import {
  type FocusEventHandler,
  type KeyboardEventHandler,
  type Ref,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import { CatalogRateLegend } from "./CatalogRateGuide";

/**
 * 模型组合槽位选择器 — 一条可筛选的平铺名单。
 *
 * 点一行钉 `@platform/{id}` / `@byok/{provider_id}/{id}`。名单在设置 · 模型：
 * 平台默认开，接入服务商的目录默认关、打开后才进弹层。
 * BYOK 随时可以手填：目录为空时选择器主体就是输入框；有目录时到设置 · 模型
 * 该渠道节底「使用其他模型」加进名单。正在用的手填 id 是被勾上的一行，点它再改。
 * 平台档不手填。筛选无命中仍写「无匹配模型」。弹层底栏只留「管理模型」。
 * 可选槽（组队 / 后台）传入 `followLabel` 时，目录最上钉这一项，筛选滤不掉，点它写回空槽。
 * `wideMenu` 把目录固定为 24rem 并右缘对齐触发器，给收窄的槽用；主模型仍跟触发器同宽。
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

/** 行尾只留不可用原因。官网倍率贴在目录行名字后。平台单价在对照表；BYOK 单价在右侧卡片。 */
export function catalogOptionTrailing(opt: DefaultModelOption): string | null {
  if (opt.available === false) {
    return unavailableReasonCopy(opt.unavailableReason);
  }
  return null;
}

function formatCatalogRate(raw: string | null | undefined): string | null {
  const value = raw?.trim();
  if (!value || value === "1") return null;
  return `${value}x`;
}

/** 相对该模型官网的卖价倍率。×1 和 BYOK 不写。 */
export function catalogOfficialRate(opt: DefaultModelOption): string | null {
  if (opt.origin !== "platform") return null;
  return formatCatalogRate(opt.price?.multiplier);
}

/** 目录行名字后的主倍率 = 官网倍率。 */
export function catalogOptionRate(opt: DefaultModelOption): string | null {
  return catalogOfficialRate(opt);
}

function formatPrice(opt: DefaultModelOption): string | null {
  const price = opt.price;
  if (!price) return null;
  const inn = price.cache_miss?.trim() || null;
  const out = price.output?.trim() || null;
  if (!inn && !out) return null;
  if (opt.origin === "platform") {
    if (inn && out) return `${inn} / ${out} 积分`;
    if (inn) return `${inn} 积分 in`;
    return `${out} 积分 out`;
  }
  const sym = unitPriceSymbol(price.currency);
  if (inn && out) return `${sym}${inn} / ${sym}${out}`;
  if (inn) return `${sym}${inn} in`;
  return `${sym}${out} out`;
}

/** 显示名里已经写了厂商（DeepSeek V4.1 Flash）就不再在副行重复。 */
function labelAlreadyNamesVendor(label: string, vendor: string): boolean {
  const name = label.trim().toLowerCase();
  const maker = vendor.trim().toLowerCase();
  if (!name || !maker) return false;
  return name.includes(maker);
}

function modelOptionFacts(
  opt: DefaultModelOption,
  includeWindow: boolean,
): string {
  if (
    opt.origin !== "platform" &&
    opt.badge &&
    opt.badge !== PLATFORM_ORIGIN_BADGE &&
    opt.model &&
    opt.label !== opt.model
  ) {
    return opt.model;
  }
  const parts: string[] = [];
  const vendor = opt.vendor?.trim() || "";
  if (vendor && !labelAlreadyNamesVendor(opt.label, vendor)) {
    parts.push(vendor);
  }
  if (includeWindow) {
    const ctx = formatContextLength(opt.contextLength);
    if (ctx) parts.push(ctx);
  }
  const price = formatPrice(opt);
  if (price) parts.push(price);
  if (isHandFilledOption(opt)) parts.push("自定义");
  return parts.join(" · ");
}

/** 设置下拉副行：窗口和单价。「平台」徽标是付款来源，不改写成裸 id。 */
export function modelOptionSecondary(opt: DefaultModelOption): string {
  return modelOptionFacts(opt, true);
}

/** 卡片标题下一行：平台描述迁到对照表；BYOK 仍单价。 */
export function modelOptionCardFacts(opt: DefaultModelOption): string {
  if (opt.origin === "platform") return "";
  return modelOptionFacts(opt, false);
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

export function groupHeading(g: DefaultProviderGroup): string {
  return g.orphan ? `${g.providerLabel}（需改选）` : g.providerLabel;
}

export function PickerCatalogFooter({
  onNavigate,
}: {
  onNavigate?: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-2.5 py-1.5">
      <Link
        to="/more/models"
        className="text-xs text-primary underline-offset-2 hover:underline"
        onClick={onNavigate}
      >
        管理模型
      </Link>
    </div>
  );
}

export function CustomModelIdField({
  value,
  disabled,
  onChange,
  onKeyDown,
  onBlur,
  inputRef,
  id,
  describedBy,
}: {
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
  onKeyDown?: KeyboardEventHandler<HTMLInputElement>;
  onBlur?: FocusEventHandler<HTMLInputElement>;
  inputRef?: Ref<HTMLInputElement>;
  id?: string;
  describedBy?: string;
}) {
  return (
    <Input
      ref={inputRef}
      id={id}
      value={value}
      disabled={disabled}
      aria-label="自定义 model id"
      aria-describedby={describedBy}
      placeholder="model id，如 ep-xxxx"
      onChange={(e) => onChange(e.target.value)}
      onKeyDown={onKeyDown}
      onBlur={onBlur}
      className="min-w-0 flex-1"
    />
  );
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
  wideMenu = false,
  menuPlacement = "below",
  rowLabel,
  hiddenRefs,
  enabledRefs,
  onChange,
}: {
  id?: string;
  groups: DefaultProviderGroup[];
  value: string;
  disabled?: boolean;
  hiddenRefs?: Iterable<string>;
  enabledRefs?: Iterable<string>;
  /** 可选槽：空值文案，并作为目录最上的钉选项（跟随主模型）。 */
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
  /**
   * 组队队员：目录 24rem，右缘对齐触发器。主模型不传，目录跟触发器同宽。
   */
  wideMenu?: boolean;
  /** 贴着弹层底边的槽向上展开，避免被弹层裁切。 */
  menuPlacement?: "below" | "above";
  /** 浅底一行：左边是这个词，右边是当前值。与思考强度、上下文长度同一套行。 */
  rowLabel?: string;
  onChange: (value: string) => void;
}) {
  const decoded = value ? decodePointer(value) : null;
  const [providerId, setProviderId] = useState(
    () => providerIdFromPointer(value) || groups[0]?.providerId || "",
  );
  const [customText, setCustomText] = useState(() => decoded?.model ?? "");
  const [draft, setDraft] = useState<{ pid: string; text: string } | null>(
    null,
  );
  const [editingProviderId, setEditingProviderId] = useState<string | null>(
    null,
  );
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const rootRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const customFieldRef = useRef<HTMLInputElement>(null);
  const listScrollRef = useRef<HTMLDivElement>(null);
  const listId = useId();

  // 目录为空时槽位本身就是输入框，外部 value 变更要跟住，但不打断正在打的字。
  useEffect(() => {
    const model = decoded?.model ?? "";
    setCustomText((prev) => (prev.trim() === model ? prev : model));
    const pid = providerIdFromPointer(value);
    if (pid) setProviderId(pid);
  }, [value, decoded?.model]);

  // 右缘对齐会在短槽落到行首时伸出左边界。量到真实宽度后再改贴左缘。
  useLayoutEffect(() => {
    if (!open) return;
    revealSelectedPickerOption(listScrollRef.current);
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !wideMenu) return;
    const panel = panelRef.current;
    const root = rootRef.current;
    if (!panel || !root) return;
    const trigger = root.getBoundingClientRect();
    const width = panel.getBoundingClientRect().width;
    if (width === 0) return;
    const overflowsLeft = trigger.right - width < 8;
    panel.style.right = overflowsLeft ? "auto" : "0px";
    panel.style.left = overflowsLeft ? "0px" : "auto";
  }, [open, wideMenu]);

  useEffect(() => {
    if (!open) return;
    // 有目录时焦点进筛选；目录为空时焦点进手填框。
    if (channelHasListedModels(groups)) searchRef.current?.focus();
    else customFieldRef.current?.focus();
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current?.contains(e.target as Node)) return;
      if (isModelPickerFloatTarget(e.target)) return;
      setOpen(false);
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
  }, [open, groups]);

  useEffect(() => {
    if (!editingProviderId) return;
    customFieldRef.current?.focus();
  }, [editingProviderId]);

  const selectedGroup = groups.find((g) => g.providerId === providerId);
  const allowCustom = Boolean(
    selectedGroup && !isPlatformGroupId(selectedGroup.providerId),
  );

  const emit = (pid: string, model: string) => {
    const trimmed = model.trim();
    if (pid && trimmed) onChange(encodePointer(pid, trimmed));
    else onChange("");
  };

  const clearCustomEdit = () => {
    setDraft(null);
    setEditingProviderId(null);
  };

  const pickCatalog = (pid: string, opt: DefaultModelOption) => {
    setProviderId(pid);
    setCustomText(opt.model);
    clearCustomEdit();
    setOpen(false);
    setQuery("");
    emit(pid, opt.model);
  };

  const pickFollow = () => {
    setCustomText("");
    clearCustomEdit();
    setOpen(false);
    setQuery("");
    onChange("");
  };

  const beginCustomEdit = (pid: string, model: string) => {
    setProviderId(pid);
    setEditingProviderId(pid);
    setDraft({ pid, text: model });
  };

  const writeCustom = (pid: string, text: string) => {
    setProviderId(pid);
    setEditingProviderId(pid);
    setDraft({ pid, text });
    setCustomText(text);
    emit(pid, text);
  };

  const openPicker = () => {
    setQuery("");
    clearCustomEdit();
    setOpen(true);
  };

  const valueProviderId = providerIdFromPointer(value);
  const valueGroup = groups.find((g) => g.providerId === valueProviderId);
  const valueIsCustom = Boolean(
    decoded?.model &&
      valueProviderId &&
      !isPlatformGroupId(valueProviderId) &&
      !modelInChannelCatalog(valueGroup, decoded.model),
  );
  const activeCustom = useMemo(
    () =>
      valueIsCustom
        ? {
            providerId: valueProviderId as string,
            model: decoded?.model ?? "",
          }
        : null,
    [valueIsCustom, valueProviderId, decoded?.model],
  );

  const pickerRows = useMemo(
    () =>
      flattenPickerRows(groups, {
        query,
        currentPointer: value,
        hiddenRefs,
        enabledRefs,
        activeCustom,
      }),
    [groups, query, value, hiddenRefs, enabledRefs, activeCustom],
  );
  const blankByok = useMemo(() => emptyByokGroups(groups), [groups]);
  const listedAnywhere = channelHasListedModels(groups);
  const showBlankInputs = !query.trim() && blankByok.length > 0;
  const hasPickerRows = pickerRows.length > 0 || showBlankInputs;
  const directInput = Boolean(
    allowCustom && !listedAnywhere && followLabel === undefined,
  );

  const customSeed = (pid: string): string => {
    if (draft?.pid === pid) return draft.text;
    if (!valueIsCustom || valueProviderId !== pid) return "";
    const model = decoded?.model ?? "";
    const q = query.trim().toLowerCase();
    if (!q || model.toLowerCase().includes(q)) return model;
    return "";
  };

  const fieldOpen = (pid: string, customInput: boolean) =>
    customInput || draft?.pid === pid || editingProviderId === pid;

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
    if (valueIsCustom || (opt && isHandFilledOption(opt))) {
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
  if (directInput) {
    const fields =
      blankByok.length > 0 ? blankByok : groups.filter((g) => !g.orphan);
    return (
      <div ref={rootRef} className="relative space-y-2">
        {fields.map((g, index) => (
          <div key={g.providerId} className="space-y-1">
            {fields.length > 1 ? (
              <p className="text-xs text-muted-foreground">{groupHeading(g)}</p>
            ) : null}
            <CustomModelIdField
              id={index === 0 ? id : undefined}
              value={
                g.providerId === providerId
                  ? customText
                  : g.providerId === valueProviderId
                    ? (decoded?.model ?? "")
                    : ""
              }
              disabled={disabled}
              describedBy={index === 0 ? describedBy : undefined}
              onChange={(text) => writeCustom(g.providerId, text)}
            />
          </div>
        ))}
        {selectedGroup?.orphan ? (
          <p className="mt-1 text-xs text-primary">
            原服务商已移除，请改选其他渠道与模型后再保存。
          </p>
        ) : null}
      </div>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <div className={rowLabel ? "flex items-center gap-2" : undefined}>
        {rowLabel ? (
          <span className="min-w-0 flex-1 truncate text-sm text-muted-foreground">
            {rowLabel}
          </span>
        ) : null}
        <button
          type="button"
          id={id}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          aria-controls={open ? listId : undefined}
          aria-label={rowLabel || undefined}
          aria-labelledby={
            rowLabel
              ? undefined
              : labelledBy && id
                ? `${labelledBy} ${id}`
                : labelledBy
          }
          aria-describedby={describedBy}
          onClick={() => {
            if (disabled) return;
            if (open) setOpen(false);
            else openPicker();
          }}
          className={cn(
            rowLabel
              ? cn(
                  "inline-flex max-w-full items-center gap-1 rounded-lg px-2 py-1 text-left text-sm text-foreground hover:bg-accent disabled:opacity-60",
                  open && "bg-accent",
                )
              : compactTrigger
                ? cn(
                    fieldSurfaceClass,
                    "flex w-full items-center gap-2 px-2.5 text-left",
                    triggerSub ? "h-auto min-h-8 py-1" : "h-8",
                  )
                : cn(
                    "mt-1.5 flex w-full items-center gap-2 rounded-lg border border-input bg-background px-2.5 text-left text-sm focus:outline-none focus:ring-1 focus:ring-ring disabled:opacity-60",
                    triggerSub ? "min-h-9 py-1.5" : "h-9",
                  ),
            !rowLabel && !decoded?.model && "text-muted-foreground",
          )}
        >
          {rowLabel ? (
            <>
              <span className="min-w-0 truncate">{trigger.title}</span>
              <ChevronDown
                size={14}
                className="shrink-0 text-muted-foreground"
              />
            </>
          ) : (
            <>
              <span className="min-w-0 flex-1">
                <span className="flex flex-wrap items-center gap-1.5">
                  <span
                    className={cn(
                      "truncate",
                      decoded?.model
                        ? "text-foreground"
                        : "text-muted-foreground",
                    )}
                  >
                    {trigger.title}
                  </span>
                  {trigger.badge ? (
                    <Badge tone="muted">{trigger.badge}</Badge>
                  ) : null}
                </span>
                {triggerSub ? (
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                    {triggerSub}
                  </span>
                ) : null}
              </span>
              <ChevronDown
                size={14}
                className="shrink-0 text-muted-foreground"
              />
            </>
          )}
        </button>
      </div>

      {open && (
        <div
          ref={panelRef}
          data-testid="model-picker-panel"
          className={cn(
            "absolute z-50 flex max-h-96 flex-col overflow-hidden rounded-lg border border-border bg-popover shadow-md",
            menuPlacement === "above" ? "bottom-full mb-1 max-h-64" : "mt-1",
            wideMenu ? "right-0 w-96" : "w-full",
          )}
        >
          {listedAnywhere ? (
            <>
              <div className="border-b border-border p-1.5">
                <SearchField
                  ref={searchRef}
                  size="sm"
                  variant="plain"
                  value={query}
                  onValueChange={(next) => {
                    setQuery(next);
                    clearCustomEdit();
                  }}
                  placeholder="筛选模型"
                  aria-label="筛选模型"
                />
              </div>
              <CatalogRateLegend
                groups={groups}
                hiddenRefs={hiddenRefs}
                enabledRefs={enabledRefs}
              />
            </>
          ) : null}
          <div
            id={listId}
            // biome-ignore lint/a11y/useSemanticElements: custom searchable catalog; native <select> cannot host this popup.
            role="listbox"
            tabIndex={-1}
            className="flex min-h-0 flex-col"
            onKeyDown={(e) => {
              if (e.key === "Escape") {
                e.stopPropagation();
                setOpen(false);
              }
            }}
          >
            {followLabel ? (
              <button
                type="button"
                // biome-ignore lint/a11y/useSemanticElements: listbox option must stay a button for Enter/click; native <option> is not focusable in this popup.
                role="option"
                aria-selected={!decoded?.model}
                className={cn(
                  "flex w-full shrink-0 items-center gap-2 border-b border-border px-2.5 py-1.5 text-left text-sm",
                  !decoded?.model
                    ? "bg-primary/10 text-foreground"
                    : "text-foreground hover:bg-accent/50",
                )}
                onClick={pickFollow}
              >
                <span className="min-w-0 flex-1 truncate">{followLabel}</span>
                {!decoded?.model ? (
                  <Check size={14} className="shrink-0 text-primary" />
                ) : null}
              </button>
            ) : null}
            <div
              ref={listScrollRef}
              className={cn(
                "overflow-y-auto py-1",
                menuPlacement === "above" ? "max-h-40" : "max-h-56",
              )}
            >
              {!hasPickerRows ? (
                <p className="px-2.5 py-2 text-xs text-muted-foreground">
                  无匹配模型
                </p>
              ) : (
                <>
                  {pickerRows.map((row) => {
                    const selected = row.pointer === value;
                    const editing =
                      isHandFilledOption(row.option) &&
                      fieldOpen(row.providerId, false);
                    const hideRow =
                      editing &&
                      row.option.model ===
                        (draft?.pid === row.providerId
                          ? draft.text.trim()
                          : decoded?.model);
                    if (hideRow) return null;
                    return (
                      <div
                        key={row.pointer}
                        data-provider-group={row.providerId}
                      >
                        <ModelOptionRow
                          option={row.option}
                          selected={selected}
                          onPick={() => {
                            if (isHandFilledOption(row.option) && selected) {
                              beginCustomEdit(row.providerId, row.option.model);
                              return;
                            }
                            pickCatalog(row.providerId, row.option);
                          }}
                        />
                      </div>
                    );
                  })}
                  {showBlankInputs
                    ? blankByok.map((g) => (
                        <div
                          key={`custom:${g.providerId}`}
                          className="px-2.5 py-1.5"
                          data-provider-group={g.providerId}
                        >
                          {blankByok.length > 1 ? (
                            <p className="mb-1 text-xs text-muted-foreground">
                              {groupHeading(g)}
                            </p>
                          ) : null}
                          <CustomModelIdField
                            inputRef={
                              editingProviderId === g.providerId ||
                              (!editingProviderId &&
                                g.providerId === blankByok[0]?.providerId)
                                ? customFieldRef
                                : undefined
                            }
                            value={customSeed(g.providerId)}
                            disabled={disabled}
                            onChange={(text) => writeCustom(g.providerId, text)}
                          />
                        </div>
                      ))
                    : null}
                  {fieldOpen(providerId, false) &&
                  !showBlankInputs &&
                  valueIsCustom ? (
                    <div className="px-2.5 py-1.5">
                      <CustomModelIdField
                        inputRef={customFieldRef}
                        value={customSeed(providerId)}
                        disabled={disabled}
                        onChange={(text) => writeCustom(providerId, text)}
                      />
                    </div>
                  ) : null}
                </>
              )}
            </div>
            <PickerCatalogFooter onNavigate={() => setOpen(false)} />
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
  const rate = catalogOptionRate(option);
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
          {rate ? (
            <span className="shrink-0 text-xs text-muted-foreground">
              {rate}
            </span>
          ) : null}
          {option.badge ? <Badge tone="muted">{option.badge}</Badge> : null}
          {isHandFilledOption(option) ? (
            <Badge tone="muted">自定义</Badge>
          ) : null}
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
 * 是否仍可继续选模型：活渠道上有目录非自定义项，或存在可用 BYOK 渠道（可手填）。
 * 仅孤儿自定义折叠项不算「可继续选」。
 */
export function canChooseFromGroups(groups: DefaultProviderGroup[]): boolean {
  const hasCatalog = groups.some(
    (g) =>
      !g.orphan && g.models.some((m) => !m.custom && m.available !== false),
  );
  const hasByok = groups.some(
    (g) => !isPlatformGroupId(g.providerId) && !g.orphan,
  );
  return hasCatalog || hasByok;
}
