import {
  SettingRow,
  SettingsAsync,
  SettingsSection,
  SettingsStack,
} from "@/components/settings";
import { SwitchboardBulkRow } from "@/components/tools/SwitchboardBulkRow";
import { IconButton, PageHeader } from "@/components/ui";
import { Switch } from "@/components/ui/Switch";
import { Input } from "@/components/ui/input";
import { SearchField } from "@/components/ui/search-field";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { useLlmProviders } from "@/hooks/useLlmProviders";
import { useModels } from "@/hooks/useModels";
import {
  buildDefaultProviderGroups,
  encodePointer,
  isPlatformGroupId,
} from "@/lib/llmDefaults";
import {
  type SettingsChannelGroup,
  clusterSettingsRows,
  optionMatchesQuery,
  settingsChannelGroups,
  settingsChannelStartsCollapsed,
} from "@/lib/modelPicker";
import { modelKeys } from "@/lib/queryKeys";
import { notifyError } from "@/lib/toast";
import { cn } from "@/lib/utils";
import {
  catalogEnabledRefs,
  putModelDisplayName,
  putPickerVisibility,
} from "@/services/models";
import { useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Pencil } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CustomModelIdField } from "./ProfileModelSelect";

/**
 * 设置 · 模型 — 账号级名单：哪些目录行出现在输入框弹层。
 * 平台默认开；接入服务商的目录默认关，在这里打开。弹层本身是平铺。
 * 接入渠道节底「使用其他模型」手填未列出的 id，写入 enabled_refs。
 * 接入行名字旁一颗铅笔，点开就地改显示名。长渠道默认收起，展开后有多家厂商则按厂商分段。
 * 关开只改名单，不改正在跑的回合。
 */

function withRow(
  hidden: string[],
  enabled: string[],
  pointer: string,
  platform: boolean,
  on: boolean,
): { hidden: string[]; enabled: string[] } {
  if (platform) {
    const nextHidden = on
      ? hidden.filter((ref) => ref !== pointer)
      : hidden.includes(pointer)
        ? hidden
        : [...hidden, pointer];
    return { hidden: nextHidden, enabled };
  }
  const nextEnabled = on
    ? enabled.includes(pointer)
      ? enabled
      : [...enabled, pointer]
    : enabled.filter((ref) => ref !== pointer);
  return { hidden, enabled: nextEnabled };
}

function channelExpanded(
  id: string,
  rowCount: number,
  searching: boolean,
  opened: Record<string, boolean>,
): boolean {
  if (searching) return true;
  if (Object.hasOwn(opened, id)) return opened[id] === true;
  return !settingsChannelStartsCollapsed(rowCount);
}

export function ModelsSettings() {
  const queryClient = useQueryClient();
  const providersQuery = useLlmProviders();
  const catalogQuery = useModels();
  const [query, setQuery] = useState("");
  const [pending, setPending] = useState(false);
  const [opened, setOpened] = useState<Record<string, boolean>>({});

  const providers = providersQuery.data?.providers ?? [];
  const catalog = catalogQuery.data;
  const hiddenRefs = catalog?.hidden_refs ?? [];
  const enabledRefs = catalogEnabledRefs(catalog);
  const groups = useMemo(
    () => buildDefaultProviderGroups(providers, catalog),
    [providers, catalog],
  );
  const q = query.trim().toLowerCase();
  const allSections = useMemo(
    () => settingsChannelGroups(groups, hiddenRefs, enabledRefs),
    [groups, hiddenRefs, enabledRefs],
  );
  const sections = useMemo(() => {
    if (!q) return allSections;
    return allSections
      .map((section) => ({
        ...section,
        rows: section.rows.filter((row) =>
          optionMatchesQuery(row.option, q, section.group.providerLabel),
        ),
      }))
      .filter((section) => section.rows.length > 0);
  }, [allSections, q]);

  const loading = providersQuery.isLoading || catalogQuery.isLoading;
  const error =
    providersQuery.isError || catalogQuery.isError
      ? "加载失败，请重试"
      : undefined;
  const empty = !loading && !error && allSections.length === 0;

  const saveVisibility = async (
    hidden: string[],
    enabled: string[],
  ): Promise<boolean> => {
    const sameHidden =
      hidden.length === hiddenRefs.length &&
      hidden.every((ref, i) => ref === hiddenRefs[i]);
    const sameEnabled =
      enabled.length === enabledRefs.length &&
      enabled.every((ref, i) => ref === enabledRefs[i]);
    if (sameHidden && sameEnabled) return true;
    setPending(true);
    try {
      const saved = await putPickerVisibility({
        refs: hidden,
        enabled_refs: enabled,
      });
      queryClient.setQueryData(modelKeys.catalog, (current) =>
        current
          ? {
              ...current,
              hidden_refs: saved.refs,
              enabled_refs: saved.enabled_refs,
            }
          : current,
      );
      return true;
    } catch (e) {
      notifyError(e, "保存失败，请重试");
      return false;
    } finally {
      setPending(false);
    }
  };

  const addCustom = async (
    providerId: string,
    model: string,
  ): Promise<boolean> => {
    const trimmed = model.trim();
    if (!trimmed || isPlatformGroupId(providerId)) return false;
    const pointer = encodePointer(providerId, trimmed);
    const known = groups.some(
      (g) =>
        g.providerId === providerId &&
        g.models.some((m) => m.model === trimmed),
    );
    const next = withRow(hiddenRefs, enabledRefs, pointer, false, true);
    const ok = await saveVisibility(next.hidden, next.enabled);
    if (ok && !known) {
      await queryClient.invalidateQueries({ queryKey: modelKeys.catalog });
    }
    return ok;
  };

  const setRow = (pointer: string, platform: boolean, on: boolean) => {
    const next = withRow(hiddenRefs, enabledRefs, pointer, platform, on);
    void saveVisibility(next.hidden, next.enabled);
  };

  const setSection = (
    rows: { pointer: string }[],
    platform: boolean,
    on: boolean,
  ) => {
    let hidden = hiddenRefs;
    let enabled = enabledRefs;
    for (const row of rows) {
      const next = withRow(hidden, enabled, row.pointer, platform, on);
      hidden = next.hidden;
      enabled = next.enabled;
    }
    void saveVisibility(hidden, enabled);
  };

  const saveName = async (pointer: string, name: string) => {
    setPending(true);
    try {
      await putModelDisplayName({
        ref: pointer,
        display_name: name.trim() ? name.trim() : null,
      });
      await queryClient.invalidateQueries({ queryKey: modelKeys.catalog });
    } catch (e) {
      notifyError(e, "改名失败，请重试");
    } finally {
      setPending(false);
    }
  };

  return (
    <div>
      <PageHeader title="模型" />
      <SettingsStack>
        {loading || error || empty ? (
          <SettingsAsync
            loading={loading}
            error={error}
            empty={empty}
            emptyLabel={
              <>
                暂无可用模型。请到{" "}
                <Link
                  to="/more/providers"
                  className="text-primary underline-offset-2 hover:underline"
                >
                  接入服务商
                </Link>
                。
              </>
            }
          />
        ) : (
          <>
            <SearchField
              size="sm"
              value={query}
              onValueChange={setQuery}
              placeholder="筛选模型"
              aria-label="筛选模型"
            />
            {sections.length === 0 ? (
              <p className="text-xs text-muted-foreground">无匹配模型</p>
            ) : (
              sections.map((section) => (
                <ChannelSection
                  key={section.group.providerId}
                  section={section}
                  searching={Boolean(q)}
                  expanded={channelExpanded(
                    section.group.providerId,
                    section.rows.length,
                    Boolean(q),
                    opened,
                  )}
                  pending={pending}
                  onToggle={() => {
                    const id = section.group.providerId;
                    const next = !channelExpanded(
                      id,
                      section.rows.length,
                      Boolean(q),
                      opened,
                    );
                    setOpened((current) => ({ ...current, [id]: next }));
                  }}
                  onSetRow={setRow}
                  onSetSection={setSection}
                  onRename={saveName}
                  onAddCustom={addCustom}
                />
              ))
            )}
          </>
        )}
      </SettingsStack>
    </div>
  );
}

function ChannelSection({
  section,
  searching,
  expanded,
  pending,
  onToggle,
  onSetRow,
  onSetSection,
  onRename,
  onAddCustom,
}: {
  section: SettingsChannelGroup;
  searching: boolean;
  expanded: boolean;
  pending: boolean;
  onToggle: () => void;
  onSetRow: (pointer: string, platform: boolean, on: boolean) => void;
  onSetSection: (
    rows: { pointer: string }[],
    platform: boolean,
    on: boolean,
  ) => void;
  onRename: (pointer: string, name: string) => void;
  onAddCustom: (providerId: string, model: string) => Promise<boolean>;
}) {
  const platform = isPlatformGroupId(section.group.providerId);
  const offCount = section.rows.filter((row) => row.hidden).length;
  const onCount = section.rows.length - offCount;
  const collapsible =
    !searching && settingsChannelStartsCollapsed(section.rows.length);
  const clusters = clusterSettingsRows(section.rows);
  const bulk = (
    <SwitchboardBulkRow
      offCount={offCount}
      total={section.rows.length}
      disabled={pending}
      onOpen={() => onSetSection(section.rows, platform, true)}
      onClose={() => onSetSection(section.rows, platform, false)}
    />
  );
  const rows = clusters.map((cluster, clusterIndex) => (
    <div key={cluster.vendor ?? "single"}>
      {cluster.vendor ? (
        <p
          className={cn(
            "px-4 py-2 text-xs text-muted-foreground",
            clusterIndex > 0 && "border-t border-border",
          )}
        >
          {cluster.vendor}
        </p>
      ) : null}
      {cluster.rows.map((row, index) => (
        <SettingRow
          key={row.pointer}
          surface="list"
          divider={index > 0}
          label={
            platform ? (
              row.option.label
            ) : (
              <ModelRowName
                name={row.option.label}
                disabled={pending}
                onSave={(next) => onRename(row.pointer, next)}
              />
            )
          }
          description={
            row.option.model !== row.option.label ? row.option.model : undefined
          }
          control={
            <Switch
              checked={!row.hidden}
              disabled={pending}
              label={`${row.hidden ? "显示" : "隐藏"} ${row.option.label}`}
              onCheckedChange={(checked) => {
                onSetRow(row.pointer, platform, checked);
              }}
            />
          }
        />
      ))}
    </div>
  ));

  const handFill = !platform ? (
    <ChannelHandFill
      pending={pending}
      onAdd={(model) => onAddCustom(section.group.providerId, model)}
    />
  ) : null;

  if (!collapsible) {
    return (
      <SettingsSection
        title={section.group.providerLabel}
        action={bulk}
        contentClassName="space-y-0"
      >
        <div className="overflow-hidden rounded-xl border border-border">
          {rows}
        </div>
        {handFill}
      </SettingsSection>
    );
  }

  return (
    <section>
      <div className="flex items-start justify-between gap-3">
        <button
          type="button"
          aria-expanded={expanded}
          aria-label={`${section.group.providerLabel}，已开 ${onCount}/${section.rows.length}`}
          onClick={onToggle}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        >
          {expanded ? (
            <ChevronDown size={14} className="shrink-0 text-muted-foreground" />
          ) : (
            <ChevronRight
              size={14}
              className="shrink-0 text-muted-foreground"
            />
          )}
          <span className="truncate text-sm font-semibold text-foreground">
            {section.group.providerLabel}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {onCount}/{section.rows.length}
          </span>
        </button>
        {bulk}
      </div>
      {expanded ? (
        <div className="mt-3 space-y-0 overflow-hidden rounded-xl border border-border">
          {rows}
        </div>
      ) : null}
      {handFill}
    </section>
  );
}

function ChannelHandFill({
  pending,
  onAdd,
}: {
  pending: boolean;
  onAdd: (model: string) => Promise<boolean>;
}) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    if (!open) return;
    done.current = false;
    ref.current?.focus();
  }, [open]);

  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    const next = text.trim();
    setOpen(false);
    setText("");
    if (save && next) void onAdd(next);
  };

  if (!open) {
    return (
      <div className="pt-2">
        <button
          type="button"
          className="text-xs text-primary underline-offset-2 hover:underline disabled:opacity-60"
          disabled={pending}
          aria-expanded={false}
          onClick={() => setOpen(true)}
        >
          使用其他模型
        </button>
      </div>
    );
  }

  return (
    <div className="pt-2">
      <CustomModelIdField
        inputRef={ref}
        value={text}
        disabled={pending}
        onChange={setText}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.preventDefault();
            finish(true);
          } else if (event.key === "Escape") {
            event.preventDefault();
            finish(false);
          }
        }}
        onBlur={() => finish(true)}
      />
    </div>
  );
}

function ModelRowName({
  name,
  disabled,
  onSave,
}: {
  name: string;
  disabled: boolean;
  onSave: (next: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(name);
  const ref = useRef<HTMLInputElement>(null);
  const done = useRef(false);

  useEffect(() => {
    setValue(name);
  }, [name]);

  useEffect(() => {
    if (!editing) return;
    done.current = false;
    ref.current?.focus();
    ref.current?.select();
  }, [editing]);

  const finish = (fn: () => void) => {
    if (done.current) return;
    done.current = true;
    fn();
    setEditing(false);
  };

  const commit = () => {
    const next = value.trim();
    if (next === name) return;
    onSave(next);
  };

  if (!editing) {
    return (
      <span className="flex min-w-0 items-center gap-1.5">
        <span className="truncate">{name}</span>
        <SimpleTooltip label="改名">
          <IconButton
            size="sm"
            disabled={disabled}
            aria-label={`改名 ${name}`}
            onClick={() => setEditing(true)}
          >
            <Pencil size={14} />
          </IconButton>
        </SimpleTooltip>
      </span>
    );
  }

  return (
    <Input
      ref={ref}
      value={value}
      maxLength={64}
      disabled={disabled}
      placeholder="目录名"
      aria-label={`改名 ${name}`}
      onChange={(event) => setValue(event.target.value)}
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          finish(commit);
        } else if (event.key === "Escape") {
          event.preventDefault();
          finish(() => setValue(name));
        }
      }}
      onBlur={() => finish(commit)}
      className="max-w-48"
    />
  );
}
