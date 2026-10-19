import { ComposerPlusBackHeader } from "@/components/chat/message-input/ComposerPlusMenu";
import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverAnchor,
  PopoverContent,
} from "@/components/ui/popover";
import { SearchField } from "@/components/ui/search-field";
import {
  type ComposerModelKnobs,
  restoreComposerKnobs,
} from "@/lib/composerModelProfile";
import { contextBudgetLabel, contextBudgetSteps } from "@/lib/contextBudget";
import {
  type DefaultModelOption,
  type DefaultProviderGroup,
  PLATFORM_POINTER_ID,
  isPlatformGroupId,
  modelInChannelCatalog,
  pointerValue,
} from "@/lib/llmDefaults";
import {
  channelHasListedModels,
  emptyByokGroups,
  flattenPickerRows,
  isHandFilledOption,
  revealSelectedPickerOption,
} from "@/lib/modelPicker";
import { cn } from "@/lib/utils";
import { catalogContextLength } from "@/lib/windowFill";
import {
  type ComposerChoice,
  ComposerKnobs,
  TUNE_TRIGGER_CLASS,
  TuneRow,
} from "@/pages/more/ComposerModelKnobs";
import {
  type ModelProfileSlot,
  slotDisplayName,
} from "@/services/llmModelProfiles";
import {
  type ModelCatalogItem,
  catalogReasoningEffort,
} from "@/services/models";
import { Check, ChevronDown } from "lucide-react";
import {
  type ReactNode,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { CatalogRateLegend } from "./CatalogRateGuide";
import {
  CustomModelIdField,
  PickerCatalogFooter,
  catalogOptionRate,
  catalogOptionTrailing,
  groupHeading,
  modelOptionCardFacts,
} from "./ProfileModelSelect";

/**
 * 输入框模型弹层。目录是一条窄列表；搜索下说明行上倍率，`?` 打开对照表。
 * 右侧卡片锚定到已选/划过的那一行，只留名字与旋钮。「＋」里卡片出现在目录下面。
 * 点一行才写入这场。
 * 有目录时底栏只留「管理模型」；未列出的 id 到设置 · 模型该渠道节底手填。
 * 目录为空时主体就是输入框。平台档不手填。
 * 组队队员点开后占用左侧目录（最上钉跟随、有返回），不另开一层。
 */

export type ShelfLayout = "columns" | "stack";

export type ComposerModelShelfProps = {
  layout: ShelfLayout;
  groups: DefaultProviderGroup[];
  main: ModelProfileSlot | null;
  worker?: ModelProfileSlot | null;
  catalogModels: ModelCatalogItem[];
  /** Account last effort / context per catalog ref. Hover/pick restore that leaf. */
  modelKnobs?: Record<string, ComposerModelKnobs>;
  hiddenRefs?: Iterable<string>;
  enabledRefs?: Iterable<string>;
  busy?: boolean;
  effort: ComposerChoice | null;
  budget: ComposerChoice | null;
  followLabel?: string;
  onPickMain: (next: ModelProfileSlot | null) => void;
  onPickWorker?: (next: ModelProfileSlot | null) => void;
  /** 浮出的卡片改的是另一行时，连主模型一起写入。 */
  onTune: (
    slot: ModelProfileSlot,
    patch: { reasoningEffort?: string | null; contextBudget?: number | null },
  ) => void;
  slots?: ReactNode;
  messages?: ReactNode;
  empty?: ReactNode;
};

function providerIdOf(slot: ModelProfileSlot | null): string {
  if (!slot?.model) return "";
  if (slot.origin === "platform" || !slot.provider_id)
    return PLATFORM_POINTER_ID;
  return slot.provider_id;
}

function isCustomSlot(
  groups: DefaultProviderGroup[],
  slot: ModelProfileSlot | null,
): boolean {
  const pid = providerIdOf(slot);
  if (!slot?.model || !pid || isPlatformGroupId(pid)) return false;
  const group = groups.find((g) => g.providerId === pid);
  return !modelInChannelCatalog(group, slot.model);
}

export function ComposerModelShelf({
  layout,
  groups,
  main,
  worker = null,
  catalogModels,
  modelKnobs,
  busy,
  effort,
  budget,
  followLabel = "跟随主模型",
  onPickMain,
  onPickWorker,
  onTune,
  slots,
  messages,
  empty,
  hiddenRefs,
  enabledRefs,
}: ComposerModelShelfProps) {
  const listId = useId();
  const searchRef = useRef<HTMLInputElement>(null);
  const customInputRef = useRef<HTMLInputElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const leaveTimer = useRef(0);
  const [query, setQuery] = useState("");
  const [draft, setDraft] = useState<{ pid: string; text: string } | null>(
    null,
  );
  const [editingProviderId, setEditingProviderId] = useState<string | null>(
    null,
  );
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [pane, setPane] = useState<"main" | "worker">("main");
  const listedAnywhere = channelHasListedModels(groups);
  const drilling = Boolean(onPickWorker) && pane === "worker";
  const activeSlot = drilling ? worker : main;
  const activePid = providerIdOf(activeSlot);
  const activeIsCustom = isCustomSlot(groups, activeSlot);

  // biome-ignore lint/correctness/useExhaustiveDependencies: drilling is the pane-switch re-run key
  useEffect(() => {
    if (!listedAnywhere) {
      customInputRef.current?.focus({ preventScroll: true });
      return;
    }
    searchRef.current?.focus({ preventScroll: true });
  }, [listedAnywhere, drilling]);

  useEffect(() => () => window.clearTimeout(leaveTimer.current), []);

  // biome-ignore lint/correctness/useExhaustiveDependencies: drilling is the pane-switch re-run key
  useLayoutEffect(() => {
    revealSelectedPickerOption(listRef.current);
  }, [drilling]);

  useEffect(() => {
    if (!editingProviderId) return;
    customInputRef.current?.focus({ preventScroll: true });
  }, [editingProviderId]);

  const activeCustom = useMemo(
    () =>
      activeIsCustom && activeSlot?.model
        ? { providerId: activePid, model: activeSlot.model }
        : null,
    [activeIsCustom, activePid, activeSlot?.model],
  );
  const currentPointer = pointerValue(activeSlot);
  const pickerRows = useMemo(
    () =>
      flattenPickerRows(groups, {
        query,
        currentPointer,
        hiddenRefs,
        enabledRefs,
        activeCustom,
      }),
    [groups, query, currentPointer, hiddenRefs, enabledRefs, activeCustom],
  );
  const blankByok = useMemo(() => emptyByokGroups(groups), [groups]);
  const showBlankInputs = !query.trim() && blankByok.length > 0;
  const hasPickerRows = pickerRows.length > 0 || showBlankInputs;
  const selectedKey =
    activeSlot?.model && !isCustomSlot(groups, activeSlot)
      ? `${activePid}\0${activeSlot.model}`
      : null;
  const hoverPreview = previewCatalog(groups, previewKey);
  const selectedPreview = !hoverPreview
    ? previewCatalog(groups, selectedKey)
    : null;
  const hovered = hoverPreview ?? selectedPreview;
  const anchorKey = previewKey ?? selectedKey;

  const clearCustomEdit = () => {
    setDraft(null);
    setEditingProviderId(null);
  };

  const customSeed = (pid: string): string => {
    if (draft?.pid === pid) return draft.text;
    if (!activeIsCustom || activePid !== pid || !activeSlot?.model) return "";
    const q = query.trim().toLowerCase();
    if (!q || activeSlot.model.toLowerCase().includes(q))
      return activeSlot.model;
    return "";
  };

  const emitSlot = (next: ModelProfileSlot | null, stay = false) => {
    if (drilling) {
      onPickWorker?.(next);
      if (!stay) {
        setPane("main");
        setPreviewKey(null);
      }
      return;
    }
    onPickMain(next);
  };

  const fieldOpen = (pid: string, customInput: boolean) =>
    customInput || draft?.pid === pid || editingProviderId === pid;

  const pickCatalog = (pid: string, opt: DefaultModelOption) => {
    if (busy || opt.available === false) return;
    clearCustomEdit();
    setQuery("");
    emitSlot({
      origin: isPlatformGroupId(pid) ? "platform" : "byok",
      provider_id: isPlatformGroupId(pid) ? null : pid,
      model: opt.model,
    });
  };

  const pickFollow = () => {
    if (busy) return;
    clearCustomEdit();
    setQuery("");
    emitSlot(null);
  };

  const beginCustomEdit = (pid: string, model: string) => {
    if (busy) return;
    setEditingProviderId(pid);
    setDraft({ pid, text: model });
  };

  const emitCustom = (pid: string, text: string) => {
    if (busy || isPlatformGroupId(pid)) return;
    setEditingProviderId(pid);
    setDraft({ pid, text });
    const trimmed = text.trim();
    if (!pid || !trimmed) {
      emitSlot(null, true);
      return;
    }
    emitSlot({ origin: "byok", provider_id: pid, model: trimmed }, true);
  };

  const workerLabel = worker
    ? slotDisplayName(worker, catalogModels) || worker.model
    : followLabel;
  const workerNav = onPickWorker ? (
    <WorkerNavRow
      label="组队队员"
      value={workerLabel}
      disabled={busy}
      onOpen={() => {
        setPane("worker");
        setQuery("");
        clearCustomEdit();
        setPreviewKey(null);
      }}
    />
  ) : null;
  const cardSlots = drilling ? undefined : (workerNav ?? slots);

  const catalog = (
    <div className="flex min-h-0 flex-col">
      {empty ? (
        <div className="p-3">{empty}</div>
      ) : (
        <>
          {drilling ? (
            <ComposerPlusBackHeader
              title="组队队员"
              onBack={() => {
                setPane("main");
                setPreviewKey(null);
                setQuery("");
                clearCustomEdit();
              }}
            />
          ) : null}
          {listedAnywhere ? (
            <>
              <div className="border-b border-border p-2">
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
              {drilling ? null : (
                <CatalogRateLegend
                  groups={groups}
                  hiddenRefs={hiddenRefs}
                  enabledRefs={enabledRefs}
                />
              )}
            </>
          ) : null}
          <div
            ref={listRef}
            id={listId}
            // biome-ignore lint/a11y/useSemanticElements: searchable catalog; native select cannot host this popup.
            role="listbox"
            aria-label={drilling ? "组队队员" : "模型"}
            tabIndex={-1}
            className={cn(
              "py-1",
              layout === "columns" && "max-h-[min(22rem,70vh)] overflow-y-auto",
            )}
          >
            {drilling ? (
              <FollowRow
                selected={!worker?.model}
                label={followLabel}
                onPick={pickFollow}
              />
            ) : null}
            {!hasPickerRows ? (
              <p className="px-2.5 py-2 text-xs text-muted-foreground">
                无匹配模型
              </p>
            ) : (
              <>
                {pickerRows.map((row) => {
                  const rowSelected = row.pointer === currentPointer;
                  const editing =
                    isHandFilledOption(row.option) &&
                    fieldOpen(row.providerId, false);
                  const hideRow =
                    editing &&
                    row.option.model ===
                      (draft?.pid === row.providerId
                        ? draft.text.trim()
                        : main?.model);
                  if (hideRow) return null;
                  return (
                    <div key={row.pointer} data-provider-group={row.providerId}>
                      <CatalogRow
                        option={row.option}
                        selected={rowSelected}
                        previewProvider={row.providerId}
                        previewModel={row.option.model}
                        onPick={() => {
                          if (isHandFilledOption(row.option) && rowSelected) {
                            beginCustomEdit(row.providerId, row.option.model);
                            return;
                          }
                          pickCatalog(row.providerId, row.option);
                        }}
                        onHover={() => {
                          window.clearTimeout(leaveTimer.current);
                          setPreviewKey(
                            `${row.providerId}\0${row.option.model}`,
                          );
                        }}
                        detail={
                          layout === "columns" &&
                          hovered &&
                          `${row.providerId}\0${row.option.model}` ===
                            anchorKey ? (
                            <AnchoredModelDetail
                              onEnter={() =>
                                window.clearTimeout(leaveTimer.current)
                              }
                              onLeave={() => {
                                window.clearTimeout(leaveTimer.current);
                                leaveTimer.current = window.setTimeout(
                                  () => setPreviewKey(null),
                                  160,
                                );
                              }}
                            >
                              <PreviewCard
                                pin="popover"
                                title={hovered.option.label}
                                facts={modelOptionCardFacts(hovered.option)}
                                effort={
                                  drilling
                                    ? null
                                    : pointerValue(hovered.slot) ===
                                          pointerValue(main) && effort
                                      ? effort
                                      : effortChoice(
                                          hovered.slot,
                                          catalogModels,
                                          modelKnobs,
                                          (token) =>
                                            onTune(hovered.slot, {
                                              reasoningEffort: token,
                                            }),
                                        )
                                }
                                budget={
                                  drilling
                                    ? null
                                    : pointerValue(hovered.slot) ===
                                          pointerValue(main) && budget
                                      ? budget
                                      : budgetChoice(
                                          hovered.slot,
                                          catalogModels,
                                          modelKnobs,
                                          (token) => {
                                            const steps = contextBudgetSteps(
                                              catalogContextLength(
                                                catalogModels,
                                                { slot: hovered.slot },
                                              ),
                                            );
                                            const picked = Number(token);
                                            const windowStep =
                                              steps?.[steps.length - 1];
                                            onTune(hovered.slot, {
                                              contextBudget:
                                                windowStep != null &&
                                                picked === windowStep
                                                  ? null
                                                  : picked,
                                            });
                                          },
                                        )
                                }
                                busy={busy}
                                slots={cardSlots}
                                messages={drilling ? undefined : messages}
                              />
                            </AnchoredModelDetail>
                          ) : null
                        }
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
                          inputRef={customInputRef}
                          value={customSeed(g.providerId)}
                          disabled={busy}
                          onChange={(text) => emitCustom(g.providerId, text)}
                        />
                      </div>
                    ))
                  : null}
                {fieldOpen(activePid, false) &&
                !showBlankInputs &&
                activeIsCustom ? (
                  <div className="px-2.5 py-1.5">
                    <CustomModelIdField
                      inputRef={customInputRef}
                      value={customSeed(activePid)}
                      disabled={busy}
                      onChange={(text) => emitCustom(activePid, text)}
                    />
                  </div>
                ) : null}
              </>
            )}
          </div>
          <PickerCatalogFooter />
          {!listedAnywhere && !drilling ? (
            <div className="border-t border-border p-3">
              <ComposerKnobs
                effort={effort}
                budget={budget}
                busy={busy}
                slots={cardSlots}
              />
              {messages ? <div className="mt-3">{messages}</div> : null}
            </div>
          ) : null}
        </>
      )}
    </div>
  );

  const hideKnobs = drilling;
  const card = hovered ? (
    <PreviewCard
      pin={layout === "stack" ? "stack" : "popover"}
      title={hovered.option.label}
      facts={modelOptionCardFacts(hovered.option)}
      effort={
        hideKnobs
          ? null
          : pointerValue(hovered.slot) === pointerValue(main) && effort
            ? effort
            : effortChoice(hovered.slot, catalogModels, modelKnobs, (token) =>
                onTune(hovered.slot, { reasoningEffort: token }),
              )
      }
      budget={
        hideKnobs
          ? null
          : pointerValue(hovered.slot) === pointerValue(main) && budget
            ? budget
            : budgetChoice(hovered.slot, catalogModels, modelKnobs, (token) => {
                const steps = contextBudgetSteps(
                  catalogContextLength(catalogModels, { slot: hovered.slot }),
                );
                const picked = Number(token);
                const windowStep = steps?.[steps.length - 1];
                onTune(hovered.slot, {
                  contextBudget:
                    windowStep != null && picked === windowStep ? null : picked,
                });
              })
      }
      busy={busy}
      slots={cardSlots}
      messages={drilling ? undefined : messages}
    />
  ) : null;

  const leave = () => {
    if (layout === "columns") {
      window.clearTimeout(leaveTimer.current);
      leaveTimer.current = window.setTimeout(() => setPreviewKey(null), 160);
      return;
    }
    setPreviewKey(null);
  };

  if (layout === "stack") {
    return (
      <div
        ref={rootRef}
        data-testid="assembly-model-row"
        data-shelf-layout="stack"
        data-catalog-pane={drilling ? "worker" : "main"}
        className="flex flex-col"
        onMouseLeave={leave}
      >
        {catalog}
        {card}
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      data-testid="assembly-model-row"
      data-shelf-layout="columns"
      data-catalog-pane={drilling ? "worker" : "main"}
      className="relative w-52"
      onMouseLeave={leave}
    >
      {catalog}
    </div>
  );
}

function previewCatalog(
  groups: DefaultProviderGroup[],
  previewKey: string | null,
): { option: DefaultModelOption; slot: ModelProfileSlot } | null {
  if (!previewKey || previewKey.startsWith("custom:")) return null;
  const sep = previewKey.indexOf("\0");
  if (sep < 0) return null;
  const pid = previewKey.slice(0, sep);
  const model = previewKey.slice(sep + 1);
  const option = groups
    .find((g) => g.providerId === pid)
    ?.models.find((m) => m.model === model);
  if (!option) return null;
  return {
    option,
    slot: {
      origin: isPlatformGroupId(pid) ? "platform" : "byok",
      provider_id: isPlatformGroupId(pid) ? null : pid,
      model: option.model,
    },
  };
}

function effortChoice(
  slot: ModelProfileSlot,
  catalogModels: ModelCatalogItem[],
  knobs: Record<string, ComposerModelKnobs> | undefined,
  onPick: (token: string) => void,
): ComposerChoice | null {
  const spec = catalogReasoningEffort(slot, catalogModels);
  if (!spec) return null;
  const stored = restoreComposerKnobs(slot, knobs, catalogModels);
  return {
    id: "profile-reasoning-effort",
    label: "思考强度",
    value: stored.reasoning_effort ?? spec.default,
    items: spec.options.map((token) => ({ value: token, label: token })),
    onChange: onPick,
  };
}

function budgetChoice(
  slot: ModelProfileSlot,
  catalogModels: ModelCatalogItem[],
  knobs: Record<string, ComposerModelKnobs> | undefined,
  onPick: (token: string) => void,
): ComposerChoice | null {
  const steps = contextBudgetSteps(
    catalogContextLength(catalogModels, { slot }),
  );
  if (!steps) return null;
  const stored = restoreComposerKnobs(slot, knobs, catalogModels);
  return {
    id: "profile-context-budget",
    label: "上下文长度",
    value: String(stored.context_budget ?? steps[steps.length - 1]),
    items: steps.map((step) => ({
      value: String(step),
      label: contextBudgetLabel(step),
    })),
    onChange: onPick,
  };
}

function PreviewCard({
  pin,
  title,
  facts,
  effort,
  budget,
  custom,
  busy,
  slots,
  messages,
}: {
  pin: "stack" | "popover";
  title?: string;
  facts?: string;
  effort?: ComposerChoice | null;
  budget?: ComposerChoice | null;
  custom?: ReactNode;
  busy?: boolean;
  slots?: ReactNode;
  messages?: ReactNode;
}) {
  const body = (
    <>
      {custom ?? (
        <div className="min-w-0">
          <p
            id="profile-main"
            title={title}
            className="text-sm font-semibold leading-snug text-foreground"
          >
            {title}
          </p>
          {facts ? (
            <p className="mt-0.5 text-xs leading-snug text-muted-foreground">
              {facts}
            </p>
          ) : null}
        </div>
      )}
      <ComposerKnobs
        effort={effort}
        budget={budget}
        busy={busy}
        slots={slots}
      />
      {messages ? <div className="mt-3">{messages}</div> : null}
    </>
  );
  if (pin === "popover") {
    return <div className="p-3">{body}</div>;
  }
  return (
    <div data-testid="composer-model-detail" className="mt-2 w-full">
      <div className="w-full rounded-xl border border-border bg-popover p-3 shadow-overlay">
        {body}
      </div>
    </div>
  );
}

function AnchoredModelDetail({
  children,
  onEnter,
  onLeave,
}: {
  children: ReactNode;
  onEnter: () => void;
  onLeave: () => void;
}) {
  return (
    <Popover open modal={false}>
      <PopoverAnchor asChild>
        <span className="pointer-events-none absolute inset-0" aria-hidden />
      </PopoverAnchor>
      <PopoverContent
        side="right"
        align="start"
        sideOffset={8}
        collisionPadding={8}
        onOpenAutoFocus={(e) => e.preventDefault()}
        onCloseAutoFocus={(e) => e.preventDefault()}
        onMouseEnter={onEnter}
        onMouseLeave={onLeave}
        className="w-52 p-0"
        data-testid="composer-model-detail"
      >
        {children}
      </PopoverContent>
    </Popover>
  );
}

function CatalogRow({
  option,
  selected,
  previewProvider,
  previewModel,
  onPick,
  onHover,
  detail,
}: {
  option: DefaultModelOption;
  selected: boolean;
  previewProvider: string;
  previewModel: string;
  onPick: () => void;
  onHover: () => void;
  detail?: ReactNode;
}) {
  const unavailable = option.available === false;
  const trailing = catalogOptionTrailing(option);
  const rate = catalogOptionRate(option);
  const row = (
    <button
      type="button"
      // biome-ignore lint/a11y/useSemanticElements: listbox option must stay a button for Enter/click; native <option> is not focusable in this popup.
      role="option"
      aria-selected={selected}
      aria-disabled={unavailable || undefined}
      data-preview-provider={previewProvider}
      data-preview-model={previewModel}
      onMouseEnter={onHover}
      onClick={onPick}
      className={cn(
        "flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-sm",
        unavailable
          ? "cursor-not-allowed opacity-60"
          : selected
            ? "bg-accent text-foreground"
            : "hover:bg-accent/50",
      )}
    >
      <span className="flex min-w-0 flex-1 items-center gap-1.5">
        <span
          className={cn(
            "min-w-0 truncate",
            unavailable ? "text-muted-foreground" : "text-foreground",
          )}
        >
          {option.label}
        </span>
        {rate ? (
          <span className="shrink-0 text-xs text-muted-foreground">{rate}</span>
        ) : null}
      </span>
      {option.badge ? <Badge tone="muted">{option.badge}</Badge> : null}
      {isHandFilledOption(option) ? <Badge tone="muted">自定义</Badge> : null}
      {trailing ? (
        <span
          className="max-w-40 shrink-0 truncate text-xs text-muted-foreground"
          title={trailing}
        >
          {trailing}
        </span>
      ) : null}
      {selected && !unavailable ? (
        <Check size={14} className="shrink-0 text-primary" />
      ) : null}
    </button>
  );
  if (!detail) return row;
  return (
    <div className="relative">
      {row}
      {detail}
    </div>
  );
}

function FollowRow({
  selected,
  label,
  onPick,
}: {
  selected: boolean;
  label: string;
  onPick: () => void;
}) {
  return (
    <button
      type="button"
      // biome-ignore lint/a11y/useSemanticElements: listbox option must stay a button for Enter/click; native <option> is not focusable in this popup.
      role="option"
      aria-selected={selected}
      className={cn(
        "flex w-full shrink-0 items-center gap-2 border-b border-border px-2.5 py-1.5 text-left text-sm",
        selected
          ? "bg-primary/10 text-foreground"
          : "text-foreground hover:bg-accent/50",
      )}
      onClick={onPick}
    >
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {selected ? <Check size={14} className="shrink-0 text-primary" /> : null}
    </button>
  );
}

function WorkerNavRow({
  label,
  value,
  disabled,
  onOpen,
}: {
  label: string;
  value: string;
  disabled?: boolean;
  onOpen: () => void;
}) {
  return (
    <TuneRow label={label}>
      <button
        type="button"
        id="profile-worker"
        aria-label={label}
        aria-haspopup="listbox"
        disabled={disabled}
        onClick={onOpen}
        className={TUNE_TRIGGER_CLASS}
      >
        <span className="min-w-0 truncate" title={value}>
          {value}
        </span>
        <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
      </button>
    </TuneRow>
  );
}
