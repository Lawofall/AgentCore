import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  type DefaultModelOption,
  type DefaultProviderGroup,
  encodePointer,
  isPlatformGroupId,
} from "@/lib/llmDefaults";
import { isPickerEnabled } from "@/lib/modelPicker";
import { HelpCircle } from "lucide-react";

export type CatalogRateGuideRow = {
  key: string;
  label: string;
  officialRate: string;
  input: string;
  output: string;
  vsDefault: string;
};

/** Platform priced rows that appear in the picker (hidden refs stay out). */
export function catalogRateGuideOptions(
  groups: readonly DefaultProviderGroup[],
  opts: {
    hiddenRefs?: Iterable<string>;
    enabledRefs?: Iterable<string>;
  } = {},
): DefaultModelOption[] {
  const seen = new Set<string>();
  const out: DefaultModelOption[] = [];
  for (const group of groups) {
    if (!isPlatformGroupId(group.providerId)) continue;
    for (const option of group.models) {
      if (option.custom || !option.price) continue;
      const origin = option.origin ?? "platform";
      if (origin !== "platform") continue;
      const pointer = encodePointer(group.providerId, option.model);
      if (!isPickerEnabled(pointer, origin, opts)) continue;
      if (seen.has(option.model)) continue;
      seen.add(option.model);
      out.push(option);
    }
  }
  return out;
}

export function catalogShowsRateLegend(
  groups: readonly DefaultProviderGroup[],
  opts: {
    hiddenRefs?: Iterable<string>;
    enabledRefs?: Iterable<string>;
  } = {},
): boolean {
  return catalogRateGuideOptions(groups, opts).some(hasNonIdentityOfficialRate);
}

export function catalogRateGuideRows(
  options: readonly DefaultModelOption[],
): CatalogRateGuideRow[] {
  return options.map((opt) => ({
    key: opt.model,
    label: opt.label,
    officialRate: officialRateCell(opt),
    input: opt.price?.cache_miss?.trim() || "—",
    output: opt.price?.output?.trim() || "—",
    vsDefault: quotaVsDefaultCell(opt),
  }));
}

export function catalogRateGuideShowsPeakNote(
  options: readonly DefaultModelOption[],
): boolean {
  return options.some((opt) => {
    if (opt.price?.quota_multiplier?.trim() === "1") return true;
    const hay = `${opt.model} ${opt.label}`.toLowerCase();
    return hay.includes("deepseek") && hay.includes("flash");
  });
}

function hasNonIdentityOfficialRate(opt: DefaultModelOption): boolean {
  if (opt.origin !== "platform") return false;
  const value = opt.price?.multiplier?.trim();
  return Boolean(value && value !== "1");
}

function officialRateCell(opt: DefaultModelOption): string {
  const raw = opt.price?.multiplier?.trim();
  if (!raw) return "—";
  return `${raw}x`;
}

function quotaVsDefaultCell(opt: DefaultModelOption): string {
  const raw = opt.price?.quota_multiplier?.trim();
  if (!raw || raw === "1") return "以此为 1";
  return `${raw}x`;
}

/**
 * Search 下横线后再一行：行上倍率相对官网价；`?` 打开平台对照表（每百万输入 / 输出）。
 * 名单没有非 1 官网倍率时不画。
 */
export function CatalogRateLegend({
  groups,
  hiddenRefs,
  enabledRefs,
}: {
  groups: readonly DefaultProviderGroup[];
  hiddenRefs?: Iterable<string>;
  enabledRefs?: Iterable<string>;
}) {
  const options = catalogRateGuideOptions(groups, { hiddenRefs, enabledRefs });
  if (!options.some(hasNonIdentityOfficialRate)) return null;
  const rows = catalogRateGuideRows(options);
  const peak = catalogRateGuideShowsPeakNote(options);
  return (
    <div
      data-catalog-rate-legend
      className="flex items-center gap-1 px-2.5 py-1"
    >
      <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
        此倍率相对该模型官网价
      </span>
      <Popover>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label="各模型积分价目"
            className="inline-flex size-5 shrink-0 items-center justify-center rounded-full text-muted-foreground/60 hover:bg-accent hover:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <HelpCircle size={12} strokeWidth={2} aria-hidden />
          </button>
        </PopoverTrigger>
        <PopoverContent
          side="right"
          align="start"
          collisionPadding={8}
          onOpenAutoFocus={(event) => event.preventDefault()}
          onCloseAutoFocus={(event) => event.preventDefault()}
          data-catalog-rate-guide
          className="w-[min(32rem,calc(100vw-2rem))] p-3"
        >
          <div className="space-y-2">
            <div className="max-h-64 overflow-auto">
              <table className="w-full text-xs">
                <caption className="sr-only">各模型积分价目</caption>
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="whitespace-nowrap py-1 pr-2 text-left font-normal">
                      模型
                    </th>
                    <th className="whitespace-nowrap px-1 py-1 text-right font-normal">
                      相对官网
                    </th>
                    <th className="whitespace-nowrap px-1 py-1 text-right font-normal">
                      每百万输入
                    </th>
                    <th className="whitespace-nowrap px-1 py-1 text-right font-normal">
                      每百万输出
                    </th>
                    <th className="whitespace-nowrap py-1 pl-2 text-right font-normal">
                      相对默认
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.key}>
                      <td
                        className="max-w-40 truncate py-1 pr-2 text-foreground"
                        title={row.label}
                      >
                        {row.label}
                      </td>
                      <td className="whitespace-nowrap px-1 py-1 text-right tabular-nums text-foreground">
                        {row.officialRate}
                      </td>
                      <td className="whitespace-nowrap px-1 py-1 text-right tabular-nums text-foreground">
                        {row.input}
                      </td>
                      <td className="whitespace-nowrap px-1 py-1 text-right tabular-nums text-foreground">
                        {row.output}
                      </td>
                      <td className="whitespace-nowrap py-1 pl-2 text-right tabular-nums text-foreground">
                        {row.vsDefault}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {peak ? (
              <p className="text-xs text-muted-foreground">
                DeepSeek 高峰时段按官网再 ×2，平台倍率仍按行上。
              </p>
            ) : null}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
