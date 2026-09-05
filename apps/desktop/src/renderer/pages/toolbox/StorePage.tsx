import { PageContainer } from "@/components/layout/PageContainer";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import {
  Badge,
  Button,
  CatalogTile,
  EmptyHint,
  IconButton,
  PageHeader,
  SearchField,
  SectionLabel,
  Textarea,
} from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { artifactColorVar } from "@/lib/catalogColors";
import { notifyError, notifySuccess } from "@/lib/toast";
import { cn } from "@/lib/utils";
import { TOOLBOX_PAGE_BACK } from "@/pages/toolbox/manual/paths";
import { ApiError } from "@/services/api";
import {
  SKILL_STORE_PAGE_SIZE,
  type SkillStoreListing,
  type SkillStoreListingDetail,
  getSkillStoreListing,
  installSkill,
  listSkillStore,
  reportSkill,
} from "@/services/skillStore";
import { Loader2, Store, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.serverMessage?.trim()) return err.serverMessage;
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return fallback;
}

function installCta(row: SkillStoreListing): {
  label: string;
  disabled: boolean;
} {
  if (row.hasUpdate) return { label: "更新", disabled: false };
  if (row.installed) return { label: "已装", disabled: true };
  return { label: "安装", disabled: false };
}

/**
 * 工具箱 · 商店：跨用户 Skill 货架。安装 = 快照复制进「我的技能」，不占官方槽。
 * 详情走本页右侧栏（对话坞只挂在聊天页）。
 */
export function StorePage() {
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [items, setItems] = useState<SkillStoreListing[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillStoreListingDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [bodyOpen, setBodyOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [q]);

  const load = useCallback(async (nextPage: number, query: string) => {
    setLoading(true);
    setError(null);
    try {
      const result = await listSkillStore({
        q: query || undefined,
        page: nextPage,
        pageSize: SKILL_STORE_PAGE_SIZE,
      });
      setItems((prev) =>
        nextPage === 1 ? result.items : [...prev, ...result.items],
      );
      setPage(result.page);
      setTotal(result.total);
    } catch (err) {
      setError(errMsg(err, "货架加载失败"));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(1, debouncedQ);
  }, [debouncedQ, load]);

  const selected = items.find((row) => row.id === openId) ?? null;

  useEffect(() => {
    if (!openId) {
      setDetail(null);
      setDetailError(null);
      setBodyOpen(false);
      setReportOpen(false);
      setReportReason("");
      return;
    }
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
    setBodyOpen(false);
    setReportOpen(false);
    setReportReason("");
    void getSkillStoreListing(openId)
      .then((row) => {
        if (!cancelled) setDetail(row);
      })
      .catch((err) => {
        if (!cancelled) setDetailError(errMsg(err, "详情加载失败"));
      });
    return () => {
      cancelled = true;
    };
  }, [openId]);

  const patchRow = (next: SkillStoreListing) => {
    setItems((prev) =>
      prev.map((row) => (row.id === next.id ? { ...row, ...next } : row)),
    );
    setDetail((prev) =>
      prev && prev.id === next.id ? { ...prev, ...next } : prev,
    );
  };

  const onInstall = async (row: SkillStoreListing) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await installSkill(row.id);
      patchRow(next);
      notifySuccess(
        row.hasUpdate ? "已更新到「我的技能」" : "已安装到「我的技能」",
      );
    } catch (err) {
      notifyError(err, row.hasUpdate ? "更新失败" : "安装失败");
    } finally {
      setBusy(false);
    }
  };

  const onReport = async () => {
    if (!openId || busy) return;
    const reason = reportReason.trim();
    if (!reason) return;
    setBusy(true);
    try {
      await reportSkill(openId, reason);
      notifySuccess("已提交举报");
      setReportOpen(false);
      setReportReason("");
    } catch (err) {
      notifyError(err, "举报失败");
    } finally {
      setBusy(false);
    }
  };

  const cta = selected ? installCta(selected) : null;
  const description = detail?.description || selected?.description || "";
  const hasMore = items.length < total && !loading;

  return (
    <PageContainer width="canvas" fill>
      <PageHeader title="商店" back={TOOLBOX_PAGE_BACK} className="shrink-0" />

      <div className="max-w-md shrink-0">
        <SearchField
          aria-label="搜索技能"
          placeholder="搜索技能"
          value={q}
          onValueChange={setQ}
        />
      </div>

      {error ? (
        <p className="mt-4 shrink-0 text-sm text-muted-foreground" role="alert">
          {error}
        </p>
      ) : null}

      <div className="mt-6 flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto pr-1">
          {loading && items.length === 0 ? (
            <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
              <Loader2 size={16} className="animate-spin" />
              加载中…
            </div>
          ) : null}

          {!loading && items.length === 0 && !error ? (
            <EmptyHint
              className="mt-10"
              title="货架还是空的"
              hint="把「我的技能」上架之后，别人就能在这里一键安装。"
            />
          ) : null}

          {items.length > 0 ? (
            <div data-testid="skill-store-shelf">
              <SectionLabel>货架</SectionLabel>
              <div className="mt-3 grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-3">
                {items.map((row) => (
                  <StoreListingCard
                    key={row.id}
                    row={row}
                    onOpen={() => setOpenId(row.id)}
                  />
                ))}
              </div>
              {hasMore ? (
                <div className="mt-4 flex justify-center">
                  <Button
                    variant="neutral"
                    disabled={loading}
                    onClick={() => void load(page + 1, debouncedQ)}
                  >
                    更多
                  </Button>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>

        {openId ? (
          <aside
            className={cn(
              "flex w-full max-w-lg shrink-0 flex-col border-l border-border bg-background",
              "max-md:absolute max-md:inset-y-0 max-md:right-0 max-md:z-10 max-md:shadow-lg",
            )}
          >
            <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-foreground">
                  {selected?.name ?? "技能"}
                </h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  装进去之后，咨询目录会看到下面这句。
                </p>
              </div>
              <IconButton
                size="sm"
                aria-label="关闭"
                onClick={() => setOpenId(null)}
              >
                <X size={16} />
              </IconButton>
            </div>

            <div
              className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4"
              data-testid="skill-store-drawer"
            >
              {detailError ? (
                <p className="text-sm text-muted-foreground" role="alert">
                  {detailError}
                </p>
              ) : null}
              <p className="text-sm text-foreground">{description}</p>
              {detail?.content ? (
                <div>
                  <Button
                    variant="ghost"
                    onClick={() => setBodyOpen((open) => !open)}
                  >
                    {bodyOpen ? "收起正文" : "展开正文"}
                  </Button>
                  {bodyOpen ? (
                    <PromptDocument
                      className="mt-2"
                      text={detail.content}
                      compact={false}
                      maxHeightClass="max-h-64"
                    />
                  ) : null}
                </div>
              ) : null}

              <div className="mt-auto flex flex-wrap items-center justify-end gap-2 border-t border-border pt-4">
                <Button
                  variant="ghost"
                  disabled={busy || !openId}
                  onClick={() => setReportOpen(true)}
                >
                  举报
                </Button>
                {cta ? (
                  <Button
                    disabled={busy || cta.disabled}
                    onClick={() => selected && void onInstall(selected)}
                  >
                    {cta.label}
                  </Button>
                ) : null}
              </div>
            </div>
          </aside>
        ) : null}
      </div>

      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>举报技能</DialogTitle>
            <DialogDescription>说明原因，我们会人工查看。</DialogDescription>
          </DialogHeader>
          <div className="block space-y-1 px-5">
            <span className="text-muted-foreground text-xs">举报原因</span>
            <Textarea
              aria-label="举报原因"
              rows={4}
              value={reportReason}
              onChange={(event) => setReportReason(event.target.value)}
              disabled={busy}
            />
          </div>
          <DialogFooter>
            <Button
              variant="neutral"
              disabled={busy || !reportReason.trim()}
              onClick={() => void onReport()}
            >
              提交举报
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </PageContainer>
  );
}

function StoreListingCard({
  row,
  onOpen,
}: {
  row: SkillStoreListing;
  onOpen: () => void;
}) {
  return (
    <CatalogTile
      icon={<Store size={18} />}
      colorVar={artifactColorVar("guidelines")}
      title={row.name}
      description={row.description}
      onClick={onOpen}
      badge={
        row.hasUpdate ? (
          <Badge tone="muted" pill>
            有更新
          </Badge>
        ) : row.installed ? (
          <Badge tone="muted" pill>
            已装
          </Badge>
        ) : null
      }
      meta={
        row.author ? (
          <p className="mt-1 text-xs text-muted-foreground/80">{row.author}</p>
        ) : null
      }
    />
  );
}
