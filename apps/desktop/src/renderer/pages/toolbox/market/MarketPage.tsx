import { PromptDocument } from "@/components/prompt/PromptDocument";
import {
  Badge,
  Button,
  CATALOG_GRID_CLASS,
  CatalogIconShell,
  CatalogTile,
  EmptyHint,
  IconButton,
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
import {
  MARKET_KINDS,
  type MarketKind,
  TOOLBOX_KIND_LABEL,
  isMarketKind,
} from "@/pages/toolbox/kinds";
import { SHELF_TILE_CLASS, ShelfRail } from "@/pages/toolbox/market/ShelfRail";
import { StoreListingCard } from "@/pages/toolbox/market/StoreListingCard";
import {
  isOfficialAuthor,
  listingCopy,
} from "@/pages/toolbox/market/listingCopy";
import { UseTemplateDialog } from "@/pages/toolbox/workflows/UseTemplateDialog";
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
import {
  WORKFLOW_STORE_PAGE_SIZE,
  type WorkflowStoreListing,
  type WorkflowStoreListingDetail,
  getWorkflowStoreListing,
  installWorkflow,
  listWorkflowStore,
  reportWorkflow,
} from "@/services/workflowStore";
import {
  type WorkflowTemplate,
  listWorkflowTemplates,
} from "@/services/workflows";
import { Loader2, Sparkles, Store, Workflow, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

const RAIL_CAP = 12;
const SKILL_GRID_CLASS = `mt-3 ${CATALOG_GRID_CLASS}`;

function errMsg(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    if (err.serverMessage?.trim()) return err.serverMessage;
  }
  if (err instanceof Error && err.message.trim()) return err.message;
  return fallback;
}

type OpenListing =
  | { kind: "skill"; id: string }
  | { kind: "workflow"; id: string };

function installCta(row: { hasUpdate: boolean; installed: boolean }): {
  label: string;
  disabled: boolean;
} {
  if (row.hasUpdate) return { label: "更新", disabled: false };
  if (row.installed) return { label: "已装", disabled: true };
  return { label: "安装", disabled: false };
}

function templateMatches(tpl: WorkflowTemplate, query: string): boolean {
  const needle = query.toLowerCase();
  return (
    tpl.title.toLowerCase().includes(needle) ||
    tpl.summary.toLowerCase().includes(needle)
  );
}

function searchLabel(kind: MarketKind | null): string {
  const skill = TOOLBOX_KIND_LABEL.skills;
  if (kind === "workflows") return "搜索工作流";
  if (kind === "skills") return `搜索${skill}`;
  return `搜索${skill}和工作流`;
}

/**
 * 工具箱 · 市场：总货架。发现首页是 App Store 式横滑货架条；
 * 种类 chip / 查看全部才进网格；搜索切到结果面。
 */
export function MarketPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const kindParam = searchParams.get("kind");
  const kind = isMarketKind(kindParam) ? kindParam : null;

  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [items, setItems] = useState<SkillStoreListing[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [wfItems, setWfItems] = useState<WorkflowStoreListing[]>([]);
  const [wfPage, setWfPage] = useState(1);
  const [wfTotal, setWfTotal] = useState(0);
  const [wfLoading, setWfLoading] = useState(true);
  const [wfError, setWfError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<OpenListing | null>(null);
  const [detail, setDetail] = useState<SkillStoreListingDetail | null>(null);
  const [wfDetail, setWfDetail] = useState<WorkflowStoreListingDetail | null>(
    null,
  );
  const [detailError, setDetailError] = useState<string | null>(null);
  const [bodyOpen, setBodyOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reportOpen, setReportOpen] = useState(false);
  const [reportReason, setReportReason] = useState("");
  const [templates, setTemplates] = useState<WorkflowTemplate[] | null>(null);
  const [templatesHint, setTemplatesHint] = useState<string | null>(null);
  const [useTarget, setUseTarget] = useState<WorkflowTemplate | null>(null);

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

  const loadWorkflows = useCallback(async (nextPage: number, query: string) => {
    setWfLoading(true);
    setWfError(null);
    try {
      const result = await listWorkflowStore({
        q: query || undefined,
        page: nextPage,
        pageSize: WORKFLOW_STORE_PAGE_SIZE,
      });
      setWfItems((prev) =>
        nextPage === 1 ? result.items : [...prev, ...result.items],
      );
      setWfPage(result.page);
      setWfTotal(result.total);
    } catch (err) {
      setWfError(errMsg(err, "工作流货架加载失败"));
    } finally {
      setWfLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(1, debouncedQ);
  }, [debouncedQ, load]);

  useEffect(() => {
    void loadWorkflows(1, debouncedQ);
  }, [debouncedQ, loadWorkflows]);

  useEffect(() => {
    let cancelled = false;
    void listWorkflowTemplates()
      .then((list) => {
        if (!cancelled) {
          setTemplates(list);
          setTemplatesHint(null);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setTemplatesHint(errMsg(err, "官方模板加载失败"));
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedSkill =
    open?.kind === "skill"
      ? (items.find((row) => row.id === open.id) ?? null)
      : null;
  const selectedWorkflow =
    open?.kind === "workflow"
      ? (wfItems.find((row) => row.id === open.id) ?? null)
      : null;
  const selected = selectedSkill ?? selectedWorkflow;

  useEffect(() => {
    if (!open) {
      setDetail(null);
      setWfDetail(null);
      setDetailError(null);
      setBodyOpen(false);
      setReportOpen(false);
      setReportReason("");
      return;
    }
    let cancelled = false;
    setDetail(null);
    setWfDetail(null);
    setDetailError(null);
    setBodyOpen(false);
    setReportOpen(false);
    setReportReason("");
    const fetch =
      open.kind === "skill"
        ? getSkillStoreListing(open.id).then((row) => {
            if (!cancelled) setDetail(row);
          })
        : getWorkflowStoreListing(open.id).then((row) => {
            if (!cancelled) setWfDetail(row);
          });
    void fetch.catch((err) => {
      if (!cancelled) setDetailError(errMsg(err, "详情加载失败"));
    });
    return () => {
      cancelled = true;
    };
  }, [open]);

  const patchSkill = (next: SkillStoreListing) => {
    setItems((prev) =>
      prev.map((row) => (row.id === next.id ? { ...row, ...next } : row)),
    );
    setDetail((prev) =>
      prev && prev.id === next.id ? { ...prev, ...next } : prev,
    );
  };

  const patchWorkflow = (next: WorkflowStoreListing) => {
    setWfItems((prev) =>
      prev.map((row) => (row.id === next.id ? { ...row, ...next } : row)),
    );
    setWfDetail((prev) =>
      prev && prev.id === next.id ? { ...prev, ...next } : prev,
    );
  };

  const onInstallSkill = async (row: SkillStoreListing) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await installSkill(row.id);
      patchSkill(next);
      notifySuccess(
        row.hasUpdate
          ? `已更新到「我的${TOOLBOX_KIND_LABEL.skills}」`
          : `已安装到「我的${TOOLBOX_KIND_LABEL.skills}」`,
      );
    } catch (err) {
      notifyError(err, row.hasUpdate ? "更新失败" : "安装失败");
    } finally {
      setBusy(false);
    }
  };

  const onInstallWorkflow = async (row: WorkflowStoreListing) => {
    if (busy) return;
    setBusy(true);
    try {
      const next = await installWorkflow(row.id);
      patchWorkflow(next);
      notifySuccess(
        row.hasUpdate
          ? `已更新到「我的${TOOLBOX_KIND_LABEL.workflows}」`
          : `已安装到「我的${TOOLBOX_KIND_LABEL.workflows}」`,
      );
    } catch (err) {
      notifyError(err, row.hasUpdate ? "更新失败" : "安装失败");
    } finally {
      setBusy(false);
    }
  };

  const onReport = async () => {
    if (!open || busy) return;
    const reason = reportReason.trim();
    if (!reason) return;
    setBusy(true);
    try {
      if (open.kind === "skill") await reportSkill(open.id, reason);
      else await reportWorkflow(open.id, reason);
      notifySuccess("已提交举报");
      setReportOpen(false);
      setReportReason("");
    } catch (err) {
      notifyError(err, "举报失败");
    } finally {
      setBusy(false);
    }
  };

  const setKind = (next: MarketKind | null) => {
    const params = new URLSearchParams(searchParams);
    if (next) params.set("kind", next);
    else params.delete("kind");
    const search = params.toString();
    setSearchParams(search ? params : {}, { replace: true });
  };

  const searching = Boolean(debouncedQ);
  const discover = !searching && kind == null;
  const showSkills = kind !== "workflows";
  const showWorkflows = kind !== "skills";
  const featured = useMemo(
    () => items.filter((row) => isOfficialAuthor(row.author)),
    [items],
  );
  const restSkills =
    featured.length > 0
      ? items.filter((row) => !isOfficialAuthor(row.author))
      : items;
  const visibleTemplates = useMemo(() => {
    const list = templates ?? [];
    if (!debouncedQ) return list;
    return list.filter((tpl) => templateMatches(tpl, debouncedQ));
  }, [templates, debouncedQ]);
  const workflowRail = discover
    ? visibleTemplates.slice(0, RAIL_CAP)
    : visibleTemplates;
  const workflowListingRail = discover ? wfItems.slice(0, RAIL_CAP) : wfItems;
  const featuredRail = discover ? featured.slice(0, RAIL_CAP) : [];
  const skillRail = discover ? restSkills.slice(0, RAIL_CAP) : items;
  const cta = selected ? installCta(selected) : null;
  const selectedCopy = selected ? listingCopy(selected) : null;
  const description =
    (open?.kind === "workflow" ? wfDetail?.description : detail?.description) ||
    selected?.description ||
    "";
  const showDescription =
    Boolean(description) && selectedCopy?.title !== description;
  const hasMore = items.length < total && !loading && !discover;
  const wfHasMore = wfItems.length < wfTotal && !wfLoading && !discover;
  const queryLabel = searchLabel(kind);
  const seeAll = (next: MarketKind) => ({
    label: "查看全部",
    onClick: () => setKind(next),
  });
  const skillsEmpty = !loading && items.length === 0 && !error;
  const templatesReady = templates !== null;
  const workflowListingsEmpty = !wfLoading && wfItems.length === 0 && !wfError;
  const workflowsEmpty =
    templatesReady && visibleTemplates.length === 0 && workflowListingsEmpty;
  const shelfError = error || wfError;
  const searchMiss =
    searching &&
    !loading &&
    !wfLoading &&
    !shelfError &&
    (!showSkills || items.length === 0) &&
    (!showWorkflows || (workflowsEmpty && !templatesHint));
  const discoverEmpty =
    discover &&
    skillsEmpty &&
    workflowListingsEmpty &&
    templatesReady &&
    templates.length === 0 &&
    !templatesHint;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-col gap-2">
        <SearchField
          aria-label={queryLabel}
          placeholder={queryLabel}
          value={q}
          onValueChange={setQ}
        />
        {/* biome-ignore lint/a11y/useSemanticElements: 筛选芯片组；fieldset 默认边框不适合货架工具条。 */}
        <div
          role="group"
          aria-label="货架种类"
          className="flex flex-wrap gap-1.5"
        >
          {MARKET_KINDS.map((id) => {
            const pressed = kind === id;
            return (
              <Badge
                key={id}
                as="button"
                type="button"
                pill
                tone={pressed ? "primary" : "muted"}
                aria-pressed={pressed}
                onClick={() => setKind(pressed ? null : id)}
              >
                {TOOLBOX_KIND_LABEL[id]}
              </Badge>
            );
          })}
        </div>
      </div>

      {shelfError ? (
        <p className="mt-4 shrink-0 text-sm text-muted-foreground" role="alert">
          {shelfError}
        </p>
      ) : null}

      <div className="mt-6 flex min-h-0 flex-1">
        <div className="min-w-0 flex-1 overflow-y-auto pr-1">
          {showWorkflows &&
          (kind === "workflows" || templatesHint || workflowRail.length > 0) ? (
            discover ? (
              workflowRail.length > 0 ? (
                <ShelfRail title="工作流 · 官方" action={seeAll("workflows")}>
                  {workflowRail.map((tpl) => (
                    <div key={tpl.id} className={SHELF_TILE_CLASS}>
                      <WorkflowTile
                        tpl={tpl}
                        onOpen={() => setUseTarget(tpl)}
                      />
                    </div>
                  ))}
                </ShelfRail>
              ) : templatesHint ? (
                <p className="mb-8 text-xs text-muted-foreground">
                  {templatesHint}
                </p>
              ) : null
            ) : (
              <section className="mb-8 space-y-3">
                <SectionLabel>
                  {searching ? "工作流" : "工作流 · 官方"}
                </SectionLabel>
                {templatesHint ? (
                  <p className="text-xs text-muted-foreground">
                    {templatesHint}
                  </p>
                ) : null}
                {workflowRail.length > 0 ? (
                  <div className={SKILL_GRID_CLASS}>
                    {workflowRail.map((tpl) => (
                      <WorkflowTile
                        key={tpl.id}
                        tpl={tpl}
                        onOpen={() => setUseTarget(tpl)}
                      />
                    ))}
                  </div>
                ) : null}
              </section>
            )
          ) : null}

          {showWorkflows &&
          (kind === "workflows" ||
            workflowListingRail.length > 0 ||
            (wfLoading && kind === "workflows")) ? (
            discover ? (
              workflowListingRail.length > 0 ? (
                <ShelfRail
                  title={TOOLBOX_KIND_LABEL.workflows}
                  action={seeAll("workflows")}
                >
                  {workflowListingRail.map((row) => (
                    <div key={row.id} className={SHELF_TILE_CLASS}>
                      <StoreListingCard
                        row={row}
                        colorVar={artifactColorVar("workflow")}
                        icon={Workflow}
                        onOpen={() => setOpen({ kind: "workflow", id: row.id })}
                      />
                    </div>
                  ))}
                </ShelfRail>
              ) : null
            ) : (
              <section
                className="mb-8 space-y-3"
                data-testid="workflow-store-shelf"
              >
                <SectionLabel>
                  {searching ? "市场上架" : TOOLBOX_KIND_LABEL.workflows}
                </SectionLabel>
                {wfLoading && wfItems.length === 0 ? (
                  <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                    <Loader2 size={16} className="animate-spin" />
                    加载中…
                  </div>
                ) : null}
                {wfItems.length > 0 ? (
                  <>
                    <div className={SKILL_GRID_CLASS}>
                      {wfItems.map((row) => (
                        <StoreListingCard
                          key={row.id}
                          row={row}
                          colorVar={artifactColorVar("workflow")}
                          icon={Workflow}
                          onOpen={() =>
                            setOpen({ kind: "workflow", id: row.id })
                          }
                        />
                      ))}
                    </div>
                    {wfHasMore ? (
                      <div className="mt-4 flex justify-center">
                        <Button
                          variant="neutral"
                          disabled={wfLoading}
                          onClick={() =>
                            void loadWorkflows(wfPage + 1, debouncedQ)
                          }
                        >
                          更多
                        </Button>
                      </div>
                    ) : null}
                  </>
                ) : !wfLoading &&
                  kind === "workflows" &&
                  !searching &&
                  visibleTemplates.length === 0 ? (
                  <EmptyHint className="py-10" title="还没有可安装的工作流" />
                ) : null}
              </section>
            )
          ) : null}

          {showSkills ? (
            <div data-testid="skill-store-shelf">
              {discover && featuredRail.length > 0 ? (
                <ShelfRail title="官方精选" action={seeAll("skills")}>
                  {featuredRail.map((row) => (
                    <div key={row.id} className={SHELF_TILE_CLASS}>
                      <StoreListingCard
                        row={row}
                        onOpen={() => setOpen({ kind: "skill", id: row.id })}
                      />
                    </div>
                  ))}
                </ShelfRail>
              ) : null}

              {discover && skillRail.length > 0 ? (
                <ShelfRail
                  title={TOOLBOX_KIND_LABEL.skills}
                  action={seeAll("skills")}
                >
                  {skillRail.map((row) => (
                    <div key={row.id} className={SHELF_TILE_CLASS}>
                      <StoreListingCard
                        row={row}
                        onOpen={() => setOpen({ kind: "skill", id: row.id })}
                      />
                    </div>
                  ))}
                </ShelfRail>
              ) : null}

              {!discover &&
              showSkills &&
              (items.length > 0 || loading || kind === "skills") ? (
                <>
                  <SectionLabel>{TOOLBOX_KIND_LABEL.skills}</SectionLabel>
                  {loading && items.length === 0 ? (
                    <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                      <Loader2 size={16} className="animate-spin" />
                      加载中…
                    </div>
                  ) : null}
                  {items.length > 0 ? (
                    <>
                      <div className={SKILL_GRID_CLASS}>
                        {items.map((row) => (
                          <StoreListingCard
                            key={row.id}
                            row={row}
                            onOpen={() =>
                              setOpen({ kind: "skill", id: row.id })
                            }
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
                    </>
                  ) : null}
                </>
              ) : null}

              {discover && loading && items.length === 0 ? (
                <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                  <Loader2 size={16} className="animate-spin" />
                  加载中…
                </div>
              ) : null}

              {skillsEmpty && !searching && kind === "skills" ? (
                <EmptyHint
                  className="mt-10"
                  title={`还没有${TOOLBOX_KIND_LABEL.skills}`}
                />
              ) : null}
            </div>
          ) : null}

          {discoverEmpty ? (
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

        {open ? (
          <aside
            className={cn(
              "flex w-full max-w-lg shrink-0 flex-col border-l border-border bg-background",
              "max-md:absolute max-md:inset-y-0 max-md:right-0 max-md:z-10 max-md:shadow-lg",
            )}
          >
            <div className="flex items-start gap-3 border-b border-border px-5 py-4">
              <CatalogIconShell
                colorVar={artifactColorVar(
                  open.kind === "workflow" ? "workflow" : "guidelines",
                )}
                size="lg"
              >
                {open.kind === "workflow" ? (
                  <Workflow size={20} />
                ) : (
                  <Store size={20} />
                )}
              </CatalogIconShell>
              <div className="min-w-0 flex-1">
                <h2 className="text-base font-semibold text-foreground">
                  {selectedCopy?.title ??
                    TOOLBOX_KIND_LABEL[
                      open.kind === "workflow" ? "workflows" : "skills"
                    ]}
                </h2>
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
              {cta ? (
                <Button
                  className="shrink-0"
                  disabled={busy || cta.disabled}
                  onClick={() => {
                    if (selectedSkill) void onInstallSkill(selectedSkill);
                    else if (selectedWorkflow)
                      void onInstallWorkflow(selectedWorkflow);
                  }}
                >
                  {cta.label}
                </Button>
              ) : null}
              <IconButton
                size="sm"
                aria-label="关闭"
                onClick={() => setOpen(null)}
              >
                <X size={16} />
              </IconButton>
            </div>

            <div
              className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-5 py-4"
              data-testid={
                open.kind === "workflow"
                  ? "workflow-store-drawer"
                  : "skill-store-drawer"
              }
            >
              {detailError ? (
                <p className="text-sm text-muted-foreground" role="alert">
                  {detailError}
                </p>
              ) : null}
              {showDescription ? (
                <p className="text-sm text-foreground">{description}</p>
              ) : null}
              {detail?.content ? (
                <div>
                  <Button
                    variant="ghost"
                    onClick={() => setBodyOpen((prev) => !prev)}
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
              {wfDetail?.definition ? (
                <div>
                  <Button
                    variant="ghost"
                    onClick={() => setBodyOpen((prev) => !prev)}
                  >
                    {bodyOpen ? "收起定义" : "展开定义"}
                  </Button>
                  {bodyOpen ? (
                    <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words text-xs text-foreground">
                      {JSON.stringify(wfDetail.definition, null, 2)}
                    </pre>
                  ) : null}
                </div>
              ) : null}

              <div className="mt-auto flex flex-wrap items-center justify-end gap-2 border-t border-border pt-4">
                <Button
                  variant="ghost"
                  disabled={busy || !open}
                  onClick={() => setReportOpen(true)}
                >
                  举报
                </Button>
              </div>
            </div>
          </aside>
        ) : null}
      </div>

      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              举报
              {open?.kind === "workflow"
                ? TOOLBOX_KIND_LABEL.workflows
                : TOOLBOX_KIND_LABEL.skills}
            </DialogTitle>
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

      <UseTemplateDialog
        open={!!useTarget}
        template={useTarget}
        onClose={() => setUseTarget(null)}
      />
    </div>
  );
}

function WorkflowTile({
  tpl,
  onOpen,
}: {
  tpl: WorkflowTemplate;
  onOpen: () => void;
}) {
  return (
    <CatalogTile
      icon={<Sparkles size={18} />}
      colorVar={artifactColorVar("workflow")}
      title={tpl.title}
      description={tpl.summary}
      accessory={
        <Badge tone="muted" pill>
          官方
        </Badge>
      }
      onClick={onOpen}
    />
  );
}
