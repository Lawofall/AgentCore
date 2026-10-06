import { PageContainer } from "@/components/layout/PageContainer";
import { PromptDocument } from "@/components/prompt/PromptDocument";
import {
  ASSEMBLY_CARD_GRID_CLASS,
  Badge,
  Button,
  CatalogIconShell,
  EmptyHint,
  SearchField,
  SectionLabel,
  Textarea,
} from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { artifactColorVar } from "@/lib/catalogColors";
import { MARKET_CATALOG_CAPTION } from "@/lib/skillStoreCopy";
import { notifyError, notifySuccess } from "@/lib/toast";
import { TOOLBOX_PROMPT_NOUN } from "@/pages/toolbox/kinds";
import { APP_PATHS } from "@/pages/toolbox/manual/paths";
import { ShelfRail } from "@/pages/toolbox/market/ShelfRail";
import { StoreListingCard } from "@/pages/toolbox/market/StoreListingCard";
import {
  isOfficialAuthor,
  listingCopy,
} from "@/pages/toolbox/market/listingCopy";
import {
  SKILL_STORE_GROUPS,
  skillStoreGroupLabel,
} from "@/pages/toolbox/market/skillStoreGroups";
import { ApiError } from "@/services/api";
import { skillBodyFromContent } from "@/services/skillCatalog";
import {
  EMPTY_SKILL_STORE_GROUPS,
  SKILL_STORE_DISCOVER_PAGE_SIZE,
  SKILL_STORE_PAGE_SIZE,
  type SkillStoreGroup,
  type SkillStoreListing,
  type SkillStoreListingDetail,
  getSkillStoreListing,
  installSkill,
  isSkillStoreGroup,
  listSkillStore,
  reportSkill,
} from "@/services/skillStore";
import { ChevronLeft, Loader2, Store } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

const SKILL_GRID_CLASS = `mt-3 ${ASSEMBLY_CARD_GRID_CLASS}`;

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.serverMessage?.trim()) return err.serverMessage;
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return fallback;
}

function installCta(row: { hasUpdate: boolean; installed: boolean }): {
  label: string;
  disabled: boolean;
} {
  if (row.hasUpdate) return { label: "更新", disabled: false };
  if (row.installed) return { label: "已装", disabled: true };
  return { label: "安装", disabled: false };
}

function searchLabel(): string {
  return `搜索${TOOLBOX_PROMPT_NOUN}`;
}

/**
 * 工具箱 · 市场：技能货架深页。进场即整库（库顶官方精选 + 有货的场景组）；
 * 分组 chip 在顶栏下一行，筛一组；搜索在顶栏右侧，切到结果面。顶栏与目录同三栏。
 */
export function MarketPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const groupParam = searchParams.get("group");
  const skillGroup = isSkillStoreGroup(groupParam) ? groupParam : null;

  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [items, setItems] = useState<SkillStoreListing[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [groups, setGroups] = useState(EMPTY_SKILL_STORE_GROUPS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [openId, setOpenId] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillStoreListingDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("");

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [q]);

  const skillPageSize =
    Boolean(debouncedQ) || skillGroup != null
      ? SKILL_STORE_PAGE_SIZE
      : SKILL_STORE_DISCOVER_PAGE_SIZE;

  const load = useCallback(
    async (nextPage: number, query: string) => {
      setLoading(true);
      setError(null);
      try {
        const result = await listSkillStore({
          q: query || undefined,
          page: nextPage,
          pageSize: skillPageSize,
          group: query ? undefined : (skillGroup ?? undefined),
        });
        setItems((prev) =>
          nextPage === 1 ? result.items : [...prev, ...result.items],
        );
        setPage(result.page);
        setTotal(result.total);
        setGroups(result.groups);
      } catch (err) {
        setError(errMsg(err, "货架加载失败"));
      } finally {
        setLoading(false);
      }
    },
    [skillGroup, skillPageSize],
  );

  useEffect(() => {
    void load(1, debouncedQ);
  }, [debouncedQ, load]);

  const selected =
    openId == null ? null : (items.find((row) => row.id === openId) ?? null);

  useEffect(() => {
    if (!openId) {
      setDetail(null);
      setDetailError(null);
      setReportOpen(false);
      setReportReason("");
      return;
    }
    let cancelled = false;
    setDetail(null);
    setDetailError(null);
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

  const patchSkill = (next: SkillStoreListing) => {
    setItems((prev) =>
      prev.map((row) => (row.id === next.id ? { ...row, ...next } : row)),
    );
    setDetail((prev) =>
      prev && prev.id === next.id ? { ...prev, ...next } : prev,
    );
  };

  const onInstallSkill = async (row: SkillStoreListing) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await installSkill(row.id);
      patchSkill(next);
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

  const setGroup = (next: SkillStoreGroup | null) => {
    const params = new URLSearchParams(searchParams);
    params.delete("kind");
    if (next) params.set("group", next);
    else params.delete("group");
    const search = params.toString();
    setSearchParams(search ? params : {}, { replace: true });
  };

  const searching = Boolean(debouncedQ);
  const browsingAll = !searching && skillGroup == null;
  const featured = useMemo(
    () => items.filter((row) => isOfficialAuthor(row.author)),
    [items],
  );
  const groupRails = useMemo(
    () =>
      SKILL_STORE_GROUPS.map((g) => ({
        ...g,
        rows: items.filter(
          (row) =>
            row.group === g.id &&
            !(browsingAll && isOfficialAuthor(row.author)),
        ),
      })).filter((g) => g.rows.length > 0),
    [items, browsingAll],
  );
  const visibleGroupChips = SKILL_STORE_GROUPS.filter((g) => groups[g.id] > 0);
  const featuredRail = browsingAll ? featured : [];
  const cta = selected ? installCta(selected) : null;
  const selectedCopy = selected ? listingCopy(selected) : null;
  const description = detail?.description || selected?.description || "";
  const showDescription =
    Boolean(description) && selectedCopy?.title !== description;
  const skillBody = detail?.content ? skillBodyFromContent(detail.content) : "";
  const hasMore = items.length < total && !loading && !error;
  const queryLabel = searchLabel();
  const skillsEmpty = !loading && items.length === 0 && !error;
  const searchMiss = searching && !loading && !error && items.length === 0;
  const catalogEmpty = browsingAll && skillsEmpty;

  return (
    <PageContainer width="canvas" fill padding="page">
      <div className="flex items-center justify-between gap-3">
        <Link
          to={`${APP_PATHS.toolbox.root}#prompts`}
          className="inline-flex h-8 shrink-0 items-center gap-1 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ChevronLeft size={16} />
          交代
        </Link>
        <h1 className="sr-only">工具箱</h1>
        <SearchField
          aria-label={queryLabel}
          placeholder={queryLabel}
          value={q}
          onValueChange={setQ}
          className="w-52"
        />
      </div>
      <div className="flex min-h-0 flex-1 flex-col">
        {!searching && visibleGroupChips.length > 0 ? (
          <fieldset className="m-0 mt-3 flex shrink-0 flex-wrap gap-1.5 border-0 p-0">
            <legend className="sr-only">提示词分组</legend>
            {visibleGroupChips.map((g) => {
              const pressed = skillGroup === g.id;
              return (
                <Badge
                  key={g.id}
                  as="button"
                  type="button"
                  pill
                  tone={pressed ? "primary" : "muted"}
                  aria-pressed={pressed}
                  onClick={() => setGroup(pressed ? null : g.id)}
                >
                  {g.label}
                </Badge>
              );
            })}
          </fieldset>
        ) : null}

        {error ? (
          <p
            className="mt-4 shrink-0 text-sm text-muted-foreground"
            role="alert"
          >
            {error}
          </p>
        ) : null}

        <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
          <div data-testid="skill-store-shelf">
            {browsingAll && featuredRail.length > 0 ? (
              <ShelfRail title="官方精选">
                {featuredRail.map((row) => (
                  <StoreListingCard
                    key={row.id}
                    row={row}
                    onOpen={() => setOpenId(row.id)}
                  />
                ))}
              </ShelfRail>
            ) : null}

            {browsingAll
              ? groupRails.map((g) => (
                  <ShelfRail key={g.id} title={g.label}>
                    {g.rows.map((row) => (
                      <StoreListingCard
                        key={row.id}
                        row={row}
                        onOpen={() => setOpenId(row.id)}
                      />
                    ))}
                  </ShelfRail>
                ))
              : null}

            {!browsingAll &&
            (items.length > 0 || loading || skillGroup != null) ? (
              <>
                {loading && items.length === 0 ? (
                  <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                    <Loader2 size={16} className="animate-spin" />
                    加载中…
                  </div>
                ) : null}
                {items.length > 0 ? (
                  <>
                    <SectionLabel>
                      {skillGroup
                        ? skillStoreGroupLabel(skillGroup)
                        : TOOLBOX_PROMPT_NOUN}
                    </SectionLabel>
                    <div className={SKILL_GRID_CLASS}>
                      {items.map((row) => (
                        <StoreListingCard
                          key={row.id}
                          row={row}
                          onOpen={() => setOpenId(row.id)}
                        />
                      ))}
                    </div>
                  </>
                ) : null}
              </>
            ) : null}

            {browsingAll && loading && items.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                <Loader2 size={16} className="animate-spin" />
                加载中…
              </div>
            ) : null}

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

            {skillsEmpty && skillGroup != null ? (
              <EmptyHint
                className="mt-10"
                title={`还没有${TOOLBOX_PROMPT_NOUN}`}
              />
            ) : null}
          </div>

          {catalogEmpty ? (
            <EmptyHint className="mt-10" title="还没有可安装的内容" />
          ) : null}

          {searchMiss ? (
            <EmptyHint
              className="mt-10"
              title="没有匹配的结果"
              hint="换个关键词试试。"
            />
          ) : null}
        </div>

        <Dialog
          open={openId !== null}
          onOpenChange={(next) => {
            if (!next && !reportOpen) setOpenId(null);
          }}
        >
          <DialogContent
            size="lg"
            className="flex max-h-[min(80vh,36rem)] flex-col"
            data-testid="skill-store-dialog"
            onPointerDownOutside={(event) => {
              if (reportOpen) event.preventDefault();
            }}
            onFocusOutside={(event) => {
              if (reportOpen) event.preventDefault();
            }}
          >
            <DialogHeader>
              <div className="flex items-center gap-3">
                <CatalogIconShell
                  colorVar={artifactColorVar("guidelines")}
                  size="lg"
                >
                  <Store size={20} />
                </CatalogIconShell>
                <div className="min-w-0">
                  <DialogTitle>
                    {selectedCopy?.title ?? TOOLBOX_PROMPT_NOUN}
                  </DialogTitle>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {[
                      selected?.author,
                      selectedCopy?.ident,
                      selected?.version ? `v${selected.version}` : null,
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </p>
                </div>
              </div>
            </DialogHeader>
            <DialogBody className="flex min-h-0 flex-1 flex-col gap-4">
              {detailError ? (
                <p className="text-sm text-muted-foreground" role="alert">
                  {detailError}
                </p>
              ) : null}
              {showDescription ? (
                <div>
                  <p className="text-muted-foreground text-xs">
                    {MARKET_CATALOG_CAPTION}
                  </p>
                  <p className="mt-1.5 text-sm text-foreground">
                    {description}
                  </p>
                </div>
              ) : null}
              {skillBody ? (
                <PromptDocument
                  text={skillBody}
                  compact={false}
                  maxHeightClass="max-h-none"
                />
              ) : null}
            </DialogBody>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={busy || !openId}
                onClick={() => setReportOpen(true)}
              >
                举报
              </Button>
              {cta ? (
                <Button
                  disabled={busy || cta.disabled}
                  onClick={() => {
                    if (selected) void onInstallSkill(selected);
                  }}
                >
                  {cta.label}
                </Button>
              ) : null}
            </DialogFooter>
          </DialogContent>
        </Dialog>

        <Dialog open={reportOpen} onOpenChange={setReportOpen}>
          <DialogContent size="md">
            <DialogHeader>
              <DialogTitle>举报{TOOLBOX_PROMPT_NOUN}</DialogTitle>
              <DialogDescription>说明原因，我们会人工查看。</DialogDescription>
            </DialogHeader>
            <DialogBody className="space-y-1">
              <span className="text-muted-foreground text-xs">举报原因</span>
              <Textarea
                aria-label="举报原因"
                rows={4}
                value={reportReason}
                onChange={(event) => setReportReason(event.target.value)}
                disabled={busy}
              />
            </DialogBody>
            <DialogFooter>
              <Button
                variant="outline"
                disabled={busy}
                onClick={() => setReportOpen(false)}
              >
                取消
              </Button>
              <Button
                disabled={busy || !reportReason.trim()}
                onClick={() => void onReport()}
              >
                提交举报
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      </div>
    </PageContainer>
  );
}
