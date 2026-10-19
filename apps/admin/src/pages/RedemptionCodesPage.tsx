import { SupplyTabs } from "@/components/SectionTabs";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Card, Page, PageHeader } from "@/components/ui/Page";
import { Select } from "@/components/ui/Select";
import {
  EmptyState,
  ErrorState,
  TableSkeleton,
} from "@/components/ui/States";
import { TableFrame, TableRow, THead, Td, Th } from "@/components/ui/Table";
import { cn, fmtTime } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type PaidPlan,
  type RedemptionCodeLine,
  type RedemptionStatus,
  createRedemptionCodes,
  listRedemptionCodes,
  voidRedemptionCode,
} from "@/services/adminRedemptionCodes";
import { Copy, RefreshCw } from "lucide-react";
import { type FormEvent, useCallback, useEffect, useState } from "react";
import { toast } from "sonner";

const SKU_LABEL: Record<PaidPlan, string> = {
  starter: "基础版",
  plus: "高级版",
  flagship: "旗舰版",
};

const STATUS_LABEL: Record<RedemptionStatus, string> = {
  unused: "未用",
  redeemed: "已兑",
  void: "作废",
};

/**
 * 供给 · 兑换码: batch-issue card secrets for 链动小铺 auto-delivery.
 * Plaintext is shown once after generate — paste into the shop inventory.
 */
export function RedemptionCodesPage() {
  const [sku, setSku] = useState<PaidPlan | "">("");
  const [status, setStatus] = useState<RedemptionStatus | "">("");
  const [rows, setRows] = useState<RedemptionCodeLine[]>([]);
  const [total, setTotal] = useState(0);
  const [unusedStarter, setUnusedStarter] = useState(0);
  const [unusedPlus, setUnusedPlus] = useState(0);
  const [unusedFlagship, setUnusedFlagship] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [issueSku, setIssueSku] = useState<PaidPlan>("plus");
  const [issueCount, setIssueCount] = useState("20");
  const [issuing, setIssuing] = useState(false);
  const [freshCodes, setFreshCodes] = useState<string[] | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await listRedemptionCodes({
        sku: sku || undefined,
        status: status || undefined,
      });
      setRows(resp.data);
      setTotal(resp.total);
      setUnusedStarter(resp.unused_starter);
      setUnusedPlus(resp.unused_plus);
      setUnusedFlagship(resp.unused_flagship);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setLoading(false);
    }
  }, [sku, status]);

  useEffect(() => {
    void load();
  }, [load]);

  async function onIssue(e: FormEvent) {
    e.preventDefault();
    const count = Number.parseInt(issueCount, 10);
    if (!Number.isFinite(count) || count < 1) return;
    setIssuing(true);
    try {
      const resp = await createRedemptionCodes({ sku: issueSku, count });
      setFreshCodes(resp.codes);
      toast.success(`已生成 ${resp.codes.length} 个${SKU_LABEL[issueSku]}兑换码`);
      void load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setIssuing(false);
    }
  }

  async function copyCodes(codes: string[]) {
    await navigator.clipboard.writeText(codes.join("\n"));
    toast.success("已复制，粘贴进链动小铺卡密库存");
  }

  async function onVoid(id: string) {
    try {
      await voidRedemptionCode(id);
      toast.success("已作废");
      void load();
    } catch (err) {
      toast.error(errorMessage(err));
    }
  }

  return (
    <Page>
      <PageHeader
        title="供给"
        note="生成后立刻复制到链动小铺对应商品。明文只出现这一次。"
        actions={
          <>
            <SupplyTabs />
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              disabled={loading}
              aria-label="刷新"
            >
              <RefreshCw size={14} className={cn(loading && "animate-spin")} />
            </Button>
          </>
        }
      />

      <div className="flex flex-col gap-5">
        <div className="flex flex-wrap gap-2 text-sm text-muted-foreground">
          <span>
            未用基础版{" "}
            <span className="tabular-nums text-foreground">{unusedStarter}</span>
          </span>
          <span>·</span>
          <span>
            未用高级版 <span className="tabular-nums text-foreground">{unusedPlus}</span>
          </span>
          <span>·</span>
          <span>
            未用旗舰版{" "}
            <span className="tabular-nums text-foreground">{unusedFlagship}</span>
          </span>
        </div>

        <Card className="p-4">
          <form
            className="flex flex-wrap items-end gap-3"
            onSubmit={(e) => void onIssue(e)}
          >
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              档位
              <Select
                aria-label="生成档位"
                value={issueSku}
                onChange={(ev) => setIssueSku(ev.target.value as PaidPlan)}
                options={[
                  { value: "starter", label: "基础版" },
                  { value: "plus", label: "高级版" },
                  { value: "flagship", label: "旗舰版" },
                ]}
              />
            </label>
            <label className="flex flex-col gap-1 text-xs text-muted-foreground">
              数量
              <Input
                type="number"
                min={1}
                max={200}
                value={issueCount}
                onChange={(ev) => setIssueCount(ev.target.value)}
                aria-label="生成数量"
                className="w-24"
              />
            </label>
            <Button type="submit" disabled={issuing}>
              生成兑换码
            </Button>
          </form>
          {freshCodes && freshCodes.length > 0 ? (
            <div className="mt-4 flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <p className="text-sm text-muted-foreground">
                  这一批明文只显示一次，关掉即无法再看。
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => void copyCodes(freshCodes)}
                >
                  <Copy size={14} />
                  复制全部
                </Button>
              </div>
              <pre className="max-h-48 overflow-auto rounded-lg border border-border bg-muted/40 p-3 font-mono text-xs">
                {freshCodes.join("\n")}
              </pre>
            </div>
          ) : null}
        </Card>

        <div className="flex flex-wrap gap-2">
          <Select
            aria-label="筛选档位"
            value={sku}
            onChange={(ev) => setSku(ev.target.value as PaidPlan | "")}
            options={[
              { value: "", label: "全部档位" },
              { value: "starter", label: "基础版" },
              { value: "plus", label: "高级版" },
              { value: "flagship", label: "旗舰版" },
            ]}
          />
          <Select
            aria-label="筛选状态"
            value={status}
            onChange={(ev) => setStatus(ev.target.value as RedemptionStatus | "")}
            options={[
              { value: "", label: "全部状态" },
              { value: "unused", label: "未用" },
              { value: "redeemed", label: "已兑" },
              { value: "void", label: "作废" },
            ]}
          />
        </div>

        {error ? <ErrorState message={error} onRetry={() => void load()} /> : null}
        {!error && loading && rows.length === 0 ? (
          <TableSkeleton rows={6} columns={6} />
        ) : null}
        {!error && !loading && rows.length === 0 ? (
          <EmptyState title="还没有兑换码" description="生成一批，再贴进链动小铺。" />
        ) : null}
        {rows.length > 0 ? (
          <TableFrame minWidth={720}>
            <THead>
              <Th>前缀</Th>
              <Th>档位</Th>
              <Th>状态</Th>
              <Th>生成</Th>
              <Th>兑换</Th>
              <Th />
            </THead>
            <tbody>
              {rows.map((row) => (
                <TableRow key={row.id}>
                  <Td className="font-mono text-xs">{row.prefix}</Td>
                  <Td>{SKU_LABEL[row.sku]}</Td>
                  <Td>
                    <Badge
                      tone={
                        row.status === "unused"
                          ? "primary"
                          : row.status === "redeemed"
                            ? "success"
                            : "neutral"
                      }
                    >
                      {STATUS_LABEL[row.status]}
                    </Badge>
                  </Td>
                  <Td className="text-muted-foreground">{fmtTime(row.created_at)}</Td>
                  <Td className="text-muted-foreground">
                    {row.redeemed_at ? fmtTime(row.redeemed_at) : "—"}
                  </Td>
                  <Td>
                    {row.status === "unused" ? (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => void onVoid(row.id)}
                      >
                        作废
                      </Button>
                    ) : null}
                  </Td>
                </TableRow>
              ))}
            </tbody>
          </TableFrame>
        ) : null}
        {total > rows.length ? (
          <p className="text-xs text-muted-foreground">共 {total} 条，当前页 {rows.length} 条。</p>
        ) : null}
      </div>
    </Page>
  );
}
