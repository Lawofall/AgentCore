import {
  type DefaultModelOption,
  type DefaultProviderGroup,
  encodePointer,
  isPlatformGroupId,
  unavailableReasonCopy,
} from "@/lib/llmDefaults";

/**
 * Composer / slot pickers flatten catalog rows; settings groups them by channel.
 *
 * Picker membership is origin-polarity: platform rows default on
 * (`hiddenRefs` opt-out); BYOK rows default off (`enabledRefs` opt-in).
 * Connecting a provider does not dump its directory into the composer.
 */

export type ActiveCustomModel = {
  providerId: string;
  model: string;
};

export type PickerRow = {
  pointer: string;
  providerId: string;
  providerLabel: string;
  option: DefaultModelOption;
};

/** Wide chip: catalog + right-hand card. Narrow / 「＋」panel: one column so the knobs stay on screen. */
export function composerShelfLayout(opts: {
  plusPanel?: boolean;
  narrow: boolean;
}): "columns" | "stack" {
  return opts.plusPanel || opts.narrow ? "stack" : "columns";
}

/** Portaled picker layers (detail card, 积分价目). Outside-click must not close the catalog. */
export const MODEL_PICKER_FLOAT_SELECTOR =
  "[data-testid=composer-model-detail], [data-catalog-rate-guide]";

export function isModelPickerFloatTarget(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    Boolean(target.closest(MODEL_PICKER_FLOAT_SELECTOR))
  );
}

export function isPickerEnabled(
  pointer: string,
  origin: DefaultModelOption["origin"],
  opts: {
    hiddenRefs?: Iterable<string>;
    enabledRefs?: Iterable<string>;
  } = {},
): boolean {
  const hidden = new Set(opts.hiddenRefs ?? []);
  if (hidden.has(pointer)) return false;
  if (origin === "platform") return true;
  const enabled = new Set(opts.enabledRefs ?? []);
  return enabled.has(pointer);
}

export function optionMatchesQuery(
  opt: DefaultModelOption,
  q: string,
  providerLabel = "",
): boolean {
  const reason = unavailableReasonCopy(opt.unavailableReason) ?? "";
  return (
    opt.label.toLowerCase().includes(q) ||
    opt.model.toLowerCase().includes(q) ||
    (opt.vendor ?? "").toLowerCase().includes(q) ||
    (opt.badge ?? "").toLowerCase().includes(q) ||
    providerLabel.toLowerCase().includes(q) ||
    reason.toLowerCase().includes(q)
  );
}

export function channelHasListedModels(
  groups: readonly DefaultProviderGroup[],
): boolean {
  return groups.some((g) => g.models.some((m) => !m.custom));
}

/** Live BYOK channels that can receive a typed model id. */
export function byokHandFillGroups(
  groups: readonly DefaultProviderGroup[],
): DefaultProviderGroup[] {
  return groups.filter((g) => !g.orphan && !isPlatformGroupId(g.providerId));
}

export function isHandFilledOption(opt: DefaultModelOption): boolean {
  return Boolean(opt.custom || opt.handFilled);
}

function listedModels(
  models: readonly DefaultModelOption[],
): DefaultModelOption[] {
  return models.filter((m) => !m.custom);
}

function mergeActiveCustom(
  models: DefaultModelOption[],
  providerId: string,
  active: ActiveCustomModel | null | undefined,
  q: string,
): DefaultModelOption[] {
  if (!active || active.providerId !== providerId) return models;
  const model = active.model.trim();
  if (!model) return models;
  if (q && !model.toLowerCase().includes(q)) return models;
  if (models.some((m) => m.model === model)) return models;
  return [{ model, label: model, custom: true, available: true }, ...models];
}

/** BYOK channels with no catalog rows — picker body is a model-id field. */
export function emptyByokGroups(
  groups: readonly DefaultProviderGroup[],
): DefaultProviderGroup[] {
  return groups.filter(
    (g) =>
      !g.orphan &&
      !isPlatformGroupId(g.providerId) &&
      listedModels(g.models).length === 0,
  );
}

/**
 * One searchable list for the composer and slot pickers.
 * Hidden refs stay out unless they are the current value.
 * Order is stable (label, then channel); the current row is marked, not moved.
 */
export function flattenPickerRows(
  groups: readonly DefaultProviderGroup[],
  opts: {
    query?: string;
    currentPointer?: string;
    hiddenRefs?: Iterable<string>;
    enabledRefs?: Iterable<string>;
    activeCustom?: ActiveCustomModel | null;
  } = {},
): PickerRow[] {
  const q = (opts.query ?? "").trim().toLowerCase();
  const current = (opts.currentPointer ?? "").trim();
  const rows: PickerRow[] = [];

  for (const group of groups) {
    const models = mergeActiveCustom(
      [...group.models],
      group.providerId,
      opts.activeCustom,
      q,
    );
    for (const option of models) {
      const pointer = encodePointer(group.providerId, option.model);
      const origin =
        option.origin ??
        (isPlatformGroupId(group.providerId) ? "platform" : "byok");
      if (
        pointer !== current &&
        !option.custom &&
        !isPickerEnabled(pointer, origin, opts)
      ) {
        continue;
      }
      if (q && !optionMatchesQuery(option, q, group.providerLabel)) continue;
      rows.push({
        pointer,
        providerId: group.providerId,
        providerLabel: group.providerLabel,
        option,
      });
    }
  }

  rows.sort((a, b) => {
    const aHand = isHandFilledOption(a.option);
    const bHand = isHandFilledOption(b.option);
    if (aHand !== bHand) {
      return aHand ? 1 : -1;
    }
    const byLabel = a.option.label.localeCompare(b.option.label, "zh");
    if (byLabel !== 0) return byLabel;
    return a.providerLabel.localeCompare(b.providerLabel, "zh");
  });
  return rows;
}

/** Native select/combobox: leave order alone, bring the checked row into view. */
export function revealSelectedPickerOption(list: ParentNode | null): void {
  const selected = list?.querySelector('[role="option"][aria-selected="true"]');
  if (!(selected instanceof HTMLElement)) return;
  selected.scrollIntoView?.({ block: "nearest" });
}

export type SettingsChannelRow = {
  pointer: string;
  option: DefaultModelOption;
  hidden: boolean;
};

export type SettingsChannelGroup = {
  group: DefaultProviderGroup;
  rows: SettingsChannelRow[];
};

export type SettingsVendorCluster = {
  vendor: string | null;
  rows: SettingsChannelRow[];
};

/** Short channels stay open. Aggregator catalogs start closed. */
export const SETTINGS_CHANNEL_COLLAPSE_AFTER = 4;

export function settingsChannelStartsCollapsed(rowCount: number): boolean {
  return rowCount > SETTINGS_CHANNEL_COLLAPSE_AFTER;
}

/**
 * Split a channel's inventory by catalog vendor when two or more makers appear.
 * A single-vendor (or unlabeled) list stays one untitled cluster.
 */
export function clusterSettingsRows(
  rows: readonly SettingsChannelRow[],
): SettingsVendorCluster[] {
  const named = new Set(
    rows
      .map((row) => row.option.vendor?.trim() ?? "")
      .filter((vendor) => vendor.length > 0),
  );
  if (named.size < 2) return [{ vendor: null, rows: [...rows] }];

  const buckets = new Map<string, SettingsChannelRow[]>();
  for (const row of rows) {
    const key = row.option.vendor?.trim() ?? "";
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  const keys = [...buckets.keys()].sort((a, b) => {
    if (!a) return 1;
    if (!b) return -1;
    return a.localeCompare(b, "zh");
  });
  return keys.map((key) => ({
    vendor: key || null,
    rows: buckets.get(key) ?? [],
  }));
}

/** Settings · 模型: full catalog, grouped by channel, with picker-off flags. */
export function settingsChannelGroups(
  groups: readonly DefaultProviderGroup[],
  hiddenRefs?: Iterable<string>,
  enabledRefs?: Iterable<string>,
): SettingsChannelGroup[] {
  return groups
    .filter((g) => !g.orphan)
    .map((group) => ({
      group,
      rows: listedModels(group.models).map((option) => {
        const pointer = encodePointer(group.providerId, option.model);
        const origin =
          option.origin ??
          (isPlatformGroupId(group.providerId) ? "platform" : "byok");
        return {
          pointer,
          option,
          hidden: !isPickerEnabled(pointer, origin, {
            hiddenRefs,
            enabledRefs,
          }),
        };
      }),
    }))
    .filter((section) => section.rows.length > 0);
}
