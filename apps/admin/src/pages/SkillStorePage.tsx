import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Card, Page, PageHeader } from "@/components/ui/Page";
import { Pagination } from "@/components/ui/Pagination";
import { Select, type SelectOption } from "@/components/ui/Select";
import { Spinner } from "@/components/ui/Spinner";
import {
  EmptyState,
  ErrorState,
  Refreshing,
  TableSkeleton,
} from "@/components/ui/States";
import { TableFrame, TableRow, THead, Td, Th } from "@/components/ui/Table";
import { useAdminListPage } from "@/hooks/useAdminListPage";
import { useFirstLoad } from "@/hooks/useFirstLoad";
import { oneOf, useUrlFilters } from "@/hooks/useUrlFilters";
import { cn, fmtTime } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  type SkillStoreListing,
  type SkillStoreListingDetail,
  type SkillStoreListingStatus,
  type SkillStoreReport,
  getSkillStoreListing,
  listSkillStoreListings,
  listSkillStoreReports,
  takedownSkillStoreListing,
} from "@/services/adminSkillStore";
import { Eye, Flag, RefreshCw, Store, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";

type Tone = "success" | "neutral" | "warning" | "destructive" | "primary";

const STATUS: Record<SkillStoreListingStatus, { label: string; tone: Tone }> = {
  published: { label: "已上架", tone: "success" },
  unpublished: { label: "作者已下架", tone: "warning" },
  taken_down: { label: "平台已下架", tone: "destructive" },
};

const PAGE_SIZE = 50;

type StatusFilter = SkillStoreListingStatus | "all";

const STATUS_FILTER_VALUES = [
  "all",
  "published",
  "unpublished",
  "taken_down",
] as const satisfies readonly StatusFilter[];

const STATUS_FILTERS: SelectOption[] = STATUS_FILTER_VALUES.map((value) => ({
  value,
  label: value === "all" ? "全部状态" : STATUS[value].label,
}));

const LISTING_FILTERS = { status: oneOf(STATUS_FILTER_VALUES, "all") };

function asStatus(raw: string): SkillStoreListingStatus {
  if (raw === "published" || raw === "unpublished" || raw === "taken_down") {
    return raw;
  }
  return "published";
}

function listingStatusOf(
  report: SkillStoreReport,
  listings: SkillStoreListing[],
): SkillStoreListingStatus {
  const fromRoster = listings.find((row) => row.id === report.listing_id);
  return asStatus(fromRoster?.status ?? report.listing_status);
}

export function SkillStorePage() {
  const [reports, setReports] = useState<SkillStoreReport[]>([]);
  const [reportsTotal, setReportsTotal] = useState(0);
  const [listings, setListings] = useState<SkillStoreListing[]>([]);
  const [listingsTotal, setListingsTotal] = useState(0);
  const [page, setPage] = useAdminListPage();
  const { values, set, reset } = useUrlFilters(LISTING_FILTERS);
  const statusFilter = values.status;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pending, setPending] = useState<SkillStoreListing | null>(null);
  const [preview, setPreview] = useState<SkillStoreListingDetail | null>(null);
  const [previewBusy, setPreviewBusy] = useState(false);

  const loadGenRef = useRef(0);
  const loadAbortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    loadAbortRef.current?.abort();
    const ac = new AbortController();
    loadAbortRef.current = ac;
    const gen = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const [reportsRes, listingsRes] = await Promise.all([
        listSkillStoreReports(
          { page, pageSize: PAGE_SIZE },
          ac.signal,
        ),
        listSkillStoreListings(
          {
            status: statusFilter === "all" ? undefined : statusFilter,
            page: 1,
            pageSize: PAGE_SIZE,
          },
          ac.signal,
        ),
      ]);
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setReports(reportsRes.data);
      setReportsTotal(reportsRes.total);
      setListings(listingsRes.data);
      setListingsTotal(listingsRes.total);
    } catch (err) {
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setError(errorMessage(err));
    } finally {
      if (!ac.signal.aborted && gen === loadGenRef.current) {
        setLoading(false);
      }
    }
  }, [page, statusFilter]);

  useEffect(() => {
    void load();
    return () => {
      loadAbortRef.current?.abort();
    };
  }, [load]);

  const applyTakenDown = (updated: SkillStoreListing) => {
    setListings((prev) => {
      const idx = prev.findIndex((row) => row.id === updated.id);
      if (idx < 0) return prev;
      const next = [...prev];
      next[idx] = updated;
      return next;
    });
    setReports((prev) =>
      prev.map((row) =>
        row.listing_id === updated.id
          ? { ...row, listing_status: updated.status }
          : row,
      ),
    );
  };

  const runTakedown = async (listing: SkillStoreListing) => {
    if (busyId) return;
    setBusyId(listing.id);
    try {
      const updated = await takedownSkillStoreListing(listing.id);
      applyTakenDown(updated);
      toast.success("已从货架下架");
      if (statusFilter === "published") void load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId(null);
      setPending(null);
    }
  };

  const openPreview = async (listingId: string) => {
    if (busyId || previewBusy) return;
    setPreviewBusy(true);
    try {
      setPreview(await getSkillStoreListing(listingId));
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setPreviewBusy(false);
    }
  };

  const listingForReport = (report: SkillStoreReport): SkillStoreListing => {
    const fromRoster = listings.find((row) => row.id === report.listing_id);
    if (fromRoster) return fromRoster;
    return {
      id: report.listing_id,
      name: report.listing_name,
      description: "",
      author: "",
      author_user_id: "",
      version_n: 0,
      status: asStatus(report.listing_status),
      updated_at: report.created_at,
    };
  };

  const filtered = statusFilter !== "all";
  const firstLoad =
    loading && reports.length === 0 && listings.length === 0 && !error;
  const freezeFilters = useFirstLoad(loading);
  const reportsOutOfRange = reports.length === 0 && reportsTotal > 0 && page > 1;

  return (
    <Page>
      <PageHeader
        title="商店"
        description="能力商店举报队列与 listing 下架。开放上架无预审，靠人举报后平台下架。"
        note="下架只从公开货架撤下，不会删除用户已安装的副本。"
        actions={
          <Button
            variant="outline"
            size="sm"
            onClick={() => void load()}
            disabled={loading}
            aria-label="刷新"
          >
            <RefreshCw size={14} className={cn(loading && "animate-spin")} />
          </Button>
        }
        filters={
          <Select
            aria-label="按 listing 状态筛选"
            value={statusFilter}
            disabled={freezeFilters}
            onChange={(e) => set({ status: e.target.value as StatusFilter })}
            options={STATUS_FILTERS}
          />
        }
      />

      {firstLoad ? (
        <TableSkeleton columns={6} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading} className="flex flex-col gap-8">
          <section>
            <div className="mb-3">
              <h2 className="text-sm font-semibold text-foreground">举报</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                用户对货架 listing 的举报。下架针对 listing，不是单条举报。
              </p>
            </div>
            {reports.length === 0 ? (
              <Card>
                {reportsOutOfRange ? (
                  <EmptyState
                    icon={Flag}
                    title="这一页没有举报"
                    description={`当前共 ${reportsTotal} 条，第 ${page} 页已超出范围。`}
                    action={
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setPage(1)}
                      >
                        回到第一页
                      </Button>
                    }
                  />
                ) : (
                  <EmptyState
                    icon={Flag}
                    title="还没有举报"
                    description="开放上架没有预审。有人举报后会出现在这里。"
                  />
                )}
              </Card>
            ) : (
              <TableFrame minWidth={960}>
                <THead>
                  <Th>Listing</Th>
                  <Th>状态</Th>
                  <Th>理由</Th>
                  <Th>举报人</Th>
                  <Th>时间</Th>
                  <Th align="right">操作</Th>
                </THead>
                <tbody>
                  {reports.map((row) => {
                    const status = listingStatusOf(row, listings);
                    const s = STATUS[status];
                    const listing = listingForReport(row);
                    const rowBusy = busyId === listing.id;
                    const anyBusy = busyId !== null;
                    return (
                      <TableRow key={row.id}>
                        <Td>
                          <div className="font-medium text-foreground">
                            {row.listing_name}
                          </div>
                        </Td>
                        <Td>
                          <Badge tone={s.tone}>{s.label}</Badge>
                        </Td>
                        <Td>
                          <div className="line-clamp-2 text-muted-foreground">
                            {row.reason}
                          </div>
                        </Td>
                        <Td className="text-muted-foreground">
                          {row.reporter || "—"}
                        </Td>
                        <Td className="whitespace-nowrap tabular-nums text-muted-foreground">
                          {fmtTime(row.created_at)}
                        </Td>
                        <Td align="right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={anyBusy || previewBusy}
                              onClick={() => void openPreview(listing.id)}
                            >
                              {previewBusy ? <Spinner /> : <Eye size={14} />}
                              看正文
                            </Button>
                            {status !== "taken_down" && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-destructive"
                                disabled={anyBusy || previewBusy}
                                onClick={() => setPending(listing)}
                              >
                                {rowBusy ? <Spinner /> : <TriangleAlert size={14} />}
                                下架
                              </Button>
                            )}
                          </div>
                        </Td>
                      </TableRow>
                    );
                  })}
                </tbody>
              </TableFrame>
            )}
            <Pagination
              page={page}
              pageSize={PAGE_SIZE}
              total={reportsTotal}
              onPageChange={setPage}
              disabled={loading}
            />
          </section>

          <section>
            <div className="mb-3">
              <h2 className="text-sm font-semibold text-foreground">Listing</h2>
              <p className="mt-0.5 text-xs text-muted-foreground">
                货架条目状态。平台下架后公开货架不再展示。
              </p>
            </div>
            {listings.length === 0 ? (
              <Card>
                <EmptyState
                  icon={Store}
                  title={
                    filtered
                      ? `没有「${STATUS[statusFilter].label}」状态的 listing`
                      : "还没有 listing"
                  }
                  description={
                    filtered
                      ? "换个状态再看，或清除筛选查看全部 listing。"
                      : "用户上架技能后会出现在这里。"
                  }
                  action={
                    filtered ? (
                      <Button variant="outline" size="sm" onClick={reset}>
                        清除筛选
                      </Button>
                    ) : undefined
                  }
                />
              </Card>
            ) : (
              <TableFrame minWidth={960}>
                <THead>
                  <Th>名称</Th>
                  <Th>作者</Th>
                  <Th>版本</Th>
                  <Th>状态</Th>
                  <Th>更新</Th>
                  <Th align="right">操作</Th>
                </THead>
                <tbody>
                  {listings.map((row) => {
                    const status = asStatus(row.status);
                    const s = STATUS[status];
                    const rowBusy = busyId === row.id;
                    const anyBusy = busyId !== null;
                    return (
                      <TableRow key={row.id}>
                        <Td>
                          <div className="font-medium text-foreground">
                            {row.name}
                          </div>
                          {row.description && (
                            <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                              {row.description}
                            </div>
                          )}
                        </Td>
                        <Td className="text-muted-foreground">
                          {row.author || "—"}
                        </Td>
                        <Td className="tabular-nums text-muted-foreground">
                          {row.version_n}
                        </Td>
                        <Td>
                          <Badge tone={s.tone}>{s.label}</Badge>
                        </Td>
                        <Td className="whitespace-nowrap tabular-nums text-muted-foreground">
                          {fmtTime(row.updated_at)}
                        </Td>
                        <Td align="right">
                          <div className="flex justify-end gap-1">
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={anyBusy || previewBusy}
                              onClick={() => void openPreview(row.id)}
                            >
                              {previewBusy ? <Spinner /> : <Eye size={14} />}
                              看正文
                            </Button>
                            {status !== "taken_down" && (
                              <Button
                                variant="ghost"
                                size="sm"
                                className="text-destructive"
                                disabled={anyBusy || previewBusy}
                                onClick={() => setPending(row)}
                              >
                                {rowBusy ? <Spinner /> : <TriangleAlert size={14} />}
                                下架
                              </Button>
                            )}
                          </div>
                        </Td>
                      </TableRow>
                    );
                  })}
                </tbody>
              </TableFrame>
            )}
          </section>
        </Refreshing>
      )}

      {pending && (
        <TakedownDialog
          listing={pending}
          busy={busyId === pending.id}
          onClose={() => setPending(null)}
          onConfirm={() => void runTakedown(pending)}
        />
      )}
      {preview && (
        <BodyDialog listing={preview} onClose={() => setPreview(null)} />
      )}
    </Page>
  );
}

function TakedownDialog({
  listing,
  busy,
  onClose,
  onConfirm,
}: {
  listing: SkillStoreListing;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  return (
    <Dialog
      open
      onClose={onClose}
      busy={busy}
      title="下架 listing"
      description={listing.name}
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant="destructive"
            size="sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <Spinner /> : <TriangleAlert size={14} />}
            确认下架
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">
        下架后公开货架不再展示该 listing，作者也不能再发新版本。已安装到各账号的副本不会被删除。
      </p>
    </Dialog>
  );
}

function BodyDialog({
  listing,
  onClose,
}: {
  listing: SkillStoreListingDetail;
  onClose: () => void;
}) {
  return (
    <Dialog
      open
      onClose={onClose}
      title={listing.name}
      description={listing.description || undefined}
      size="lg"
      footer={
        <Button variant="outline" size="sm" onClick={onClose}>
          关闭
        </Button>
      }
    >
      <pre className="whitespace-pre-wrap break-words text-sm text-foreground">
        {listing.content || "（没有正文）"}
      </pre>
    </Dialog>
  );
}
