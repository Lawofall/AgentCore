import { SettingsSection } from "@/components/settings";
import { Button, Card, EmptyHint, IconButton } from "@/components/ui";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  formatCostBill,
  formatMessageTime,
  formatTokenFlow,
  formatUsageCount,
  splitAccountSpend,
} from "@/lib/format";
import { cn } from "@/lib/utils";
import {
  type CostBreakdown,
  type ModelCostLine,
  type UsageActivityLine,
  type UsageBreakdown,
  getUsageActivity,
} from "@/services/usage";
import { cacheUsageDisplay } from "@agentcore/protocol-fold-kit";
import { Check, ChevronDown, ChevronLeft, ChevronRight } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

const ACTIVITY_WINDOW = "month" as const;
const EMPTY_TITLE = "本月还没有花费";

function formatRowSpend(
  billed: number,
  extra: number,
  billedCurrency?: string | null,
  extraCurrency?: string | null,
): string {
  const split = splitAccountSpend({
    billed,
    billedCurrency,
    extra,
    extraCurrency,
  });
  return formatCostBill(split.billed, split.extra) ?? "—";
}

function TokenCell({ row }: { row: UsageActivityLine }) {
  const cache = cacheUsageDisplay(row.usage);
  return (
    <p className="min-w-0 whitespace-nowrap text-xs tabular-nums text-foreground">
      {formatTokenFlow(row.usage.input, row.usage.output, cache.hitRatePercent)}
    </p>
  );
}

function ModelFilter({
  models,
  selected,
  onSelect,
}: {
  models: ModelCostLine[];
  selected: string;
  onSelect: (model: string) => void;
}) {
  if (models.length === 0) return <>模型</>;
  const current = models.find((row) => row.model === selected)?.model || "模型";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          aria-label="按模型筛选"
          className={cn(
            "inline-flex max-w-full items-center gap-0.5 font-medium",
            selected ? "text-foreground" : "text-muted-foreground",
          )}
        >
          <span className="truncate">{current}</span>
          <ChevronDown size={12} className="shrink-0" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuItem onSelect={() => onSelect("")}>
          <Check
            size={14}
            className={cn(
              "shrink-0",
              selected ? "opacity-0" : "text-foreground",
            )}
          />
          <span className="min-w-0 flex-1 truncate">全部</span>
        </DropdownMenuItem>
        {models.map((row) => {
          const label = row.model || "（未标注）";
          const active = selected === row.model;
          return (
            <DropdownMenuItem
              key={row.model}
              onSelect={() => onSelect(active ? "" : row.model)}
            >
              <Check
                size={14}
                className={cn(
                  "shrink-0",
                  active ? "text-foreground" : "opacity-0",
                )}
              />
              <span className="min-w-0 flex-1 truncate">{label}</span>
            </DropdownMenuItem>
          );
        })}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function ActivitySummary({
  usage,
  cost,
  estimatedCost,
}: {
  usage: UsageBreakdown;
  cost: CostBreakdown;
  estimatedCost?: CostBreakdown | null;
}) {
  const spend = formatRowSpend(
    cost.total,
    estimatedCost?.total ?? 0,
    cost.currency,
    estimatedCost?.currency ?? cost.currency,
  );
  const cache = cacheUsageDisplay(usage);
  return (
    <p className="mb-3 text-sm tabular-nums">
      <span
        className={
          spend === "—" ? "text-muted-foreground" : "font-medium text-success"
        }
      >
        {spend}
      </span>
      <span className="text-muted-foreground">
        {" "}
        · {formatTokenFlow(usage.input, usage.output, cache.hitRatePercent)}
      </span>
    </p>
  );
}

function ActivityPager({
  page,
  pageSize,
  total,
  disabled,
  onPageChange,
}: {
  page: number;
  pageSize: number;
  total: number;
  disabled: boolean;
  onPageChange: (page: number) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const current = Math.min(page, totalPages);
  const first = total === 0 ? 0 : (current - 1) * pageSize + 1;
  const last = Math.min(current * pageSize, total);
  return (
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
      <span className="tabular-nums">
        {total === 0
          ? "共 0 条"
          : `第 ${formatUsageCount(first)}–${formatUsageCount(last)} 条 · 共 ${formatUsageCount(total)} 条`}
      </span>
      {totalPages > 1 ? (
        <div className="flex items-center gap-2">
          <IconButton
            size="sm"
            aria-label="上一页"
            disabled={disabled || current <= 1}
            onClick={() => onPageChange(current - 1)}
          >
            <ChevronLeft size={14} />
          </IconButton>
          <span className="tabular-nums">
            {formatUsageCount(current)} / {formatUsageCount(totalPages)}
          </span>
          <IconButton
            size="sm"
            aria-label="下一页"
            disabled={disabled || current >= totalPages}
            onClick={() => onPageChange(current + 1)}
          >
            <ChevronRight size={14} />
          </IconButton>
        </div>
      ) : null}
    </div>
  );
}

function ActivityRow({
  row,
}: {
  row: UsageActivityLine;
}) {
  const models = (row.models ?? []).filter(Boolean).join(" · ");
  const spend = formatRowSpend(
    row.cost.total,
    row.estimated_cost?.total ?? 0,
    row.cost.currency,
    row.estimated_cost?.currency ?? row.cost.currency,
  );
  const href =
    row.kind === "conversation" && row.conversation_id && !row.deleted
      ? `/conversations/${row.conversation_id}`
      : null;
  const title = (
    <span className="block min-w-0 truncate" title={row.title}>
      {row.title}
    </span>
  );

  return (
    <tr
      className={cn(
        "[&>td]:border-b [&>td]:border-border last:[&>td]:border-b-0",
        href && "cursor-pointer hover:[&>td]:bg-accent",
      )}
    >
      <td className="max-w-48 px-4 py-3 text-sm text-foreground">
        {href ? (
          <Link
            to={href}
            className="block min-w-0 truncate hover:no-underline"
            onClick={(event) => event.stopPropagation()}
          >
            {title}
          </Link>
        ) : (
          title
        )}
      </td>
      <td
        className="max-w-40 truncate px-3 py-3 text-sm text-foreground"
        title={models || undefined}
      >
        {models || "—"}
      </td>
      <td className="px-3 py-3">
        <TokenCell row={row} />
      </td>
      <td
        className={cn(
          "whitespace-nowrap px-3 py-3 text-right text-sm font-medium tabular-nums",
          spend === "—" ? "text-muted-foreground" : "text-success",
        )}
      >
        {spend}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-right text-xs tabular-nums text-muted-foreground">
        {formatMessageTime(row.last_at)}
      </td>
    </tr>
  );
}

export function UsageActivity({
  models,
}: {
  models: ModelCostLine[];
}) {
  const [model, setModel] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  const [rows, setRows] = useState<UsageActivityLine[]>([]);
  const [total, setTotal] = useState(0);
  const [usage, setUsage] = useState<UsageBreakdown | null>(null);
  const [cost, setCost] = useState<CostBreakdown | null>(null);
  const [estimatedCost, setEstimatedCost] = useState<CostBreakdown | null>(
    null,
  );
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retry, setRetry] = useState(0);

  // biome-ignore lint/correctness/useExhaustiveDependencies: retry is the refetch key
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void getUsageActivity({
      window: ACTIVITY_WINDOW,
      model: model || undefined,
      page,
    })
      .then((res) => {
        if (cancelled) return;
        setTotal(res.total);
        setPageSize(res.page_size);
        setUsage(res.usage);
        setCost(res.cost);
        setEstimatedCost(res.estimated_cost ?? null);
        setRows(res.data);
        setLoading(false);
      })
      .catch(() => {
        if (cancelled) return;
        if (page === 1) {
          setRows([]);
          setUsage(null);
          setCost(null);
          setEstimatedCost(null);
        }
        setError("活动加载失败，请重试");
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [model, page, retry]);

  const changeModel = (next: string) => {
    setPage(1);
    setRows([]);
    setModel(next);
  };

  const showTable = models.length > 0 || rows.length > 0;

  return (
    <SettingsSection title="最近活动">
      {usage && cost ? (
        <ActivitySummary
          usage={usage}
          cost={cost}
          estimatedCost={estimatedCost}
        />
      ) : null}
      {error ? (
        <Card className="flex items-center justify-between gap-3 px-4 py-2.5">
          <p className="text-xs text-muted-foreground">{error}</p>
          <Button
            variant="neutral"
            onClick={() => {
              setPage(1);
              setRetry((n) => n + 1);
            }}
          >
            重试
          </Button>
        </Card>
      ) : !showTable && loading ? null : !showTable ? (
        <EmptyHint title={EMPTY_TITLE} inline className="py-6" />
      ) : (
        <div className="overflow-x-auto">
          <table
            aria-label="最近活动"
            className="w-full min-w-[36rem] border-separate border-spacing-0 text-left"
          >
            <thead>
              <tr className="text-xs font-medium text-muted-foreground">
                <th
                  scope="col"
                  className="rounded-l-full bg-muted/50 px-4 py-2 font-medium"
                >
                  对话
                </th>
                <th scope="col" className="bg-muted/50 px-3 py-2 font-medium">
                  <ModelFilter
                    models={models}
                    selected={model}
                    onSelect={changeModel}
                  />
                </th>
                <th scope="col" className="bg-muted/50 px-3 py-2 font-medium">
                  TOKEN
                </th>
                <th
                  scope="col"
                  className="bg-muted/50 px-3 py-2 text-right font-medium"
                >
                  费用
                </th>
                <th
                  scope="col"
                  className="rounded-r-full bg-muted/50 px-4 py-2 text-right font-medium"
                >
                  时间
                </th>
              </tr>
              <tr>
                <td colSpan={5} className="h-1 p-0" aria-hidden />
              </tr>
            </thead>
            <tbody>
              {rows.length === 0 ? (
                loading ? null : (
                  <tr>
                    <td colSpan={5}>
                      <EmptyHint title={EMPTY_TITLE} inline className="py-6" />
                    </td>
                  </tr>
                )
              ) : (
                rows.map((row, i) => (
                  <ActivityRow
                    key={`${row.conversation_id ?? "assist"}-${row.last_at}-${i}`}
                    row={row}
                  />
                ))
              )}
            </tbody>
          </table>
        </div>
      )}
      {rows.length > 0 ? (
        <ActivityPager
          page={page}
          pageSize={pageSize}
          total={total}
          disabled={loading}
          onPageChange={setPage}
        />
      ) : null}
    </SettingsSection>
  );
}
