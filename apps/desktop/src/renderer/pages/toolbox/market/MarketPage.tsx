import { PromptDocument } from "@/components/prompt/PromptDocument";
import {
  Badge,
  Button,
  CATALOG_GRID_CLASS,
  CatalogIconShell,
  CatalogTile,
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
import { notifyError, notifySuccess } from "@/lib/toast";
import {
  MARKET_KINDS,
  type MarketKind,
  TOOLBOX_KIND_LABEL,
  isMarketKind,
} from "@/pages/toolbox/kinds";
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
import { UseTemplateDialog } from "@/pages/toolbox/workflows/UseTemplateDialog";
import { ApiError } from "@/services/api";
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
import { Loader2, Sparkles, Store, Workflow } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

const SHELF_CAP = 12;
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
 * 工具箱 · 市场：总货架。发现首页按集合折行网格（封顶 + 查看全部）；
 * 种类 chip / 查看全部进种类或分组网格；搜索切到结果面。
 */
export function MarketPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const kindParam = searchParams.get("kind");
  const kind = isMarketKind(kindParam) ? kindParam : null;
  const groupParam = searchParams.get("group");
  const skillGroup =
    kind === "workflows"
      ? null
      : isSkillStoreGroup(groupParam)
        ? groupParam
        : null;

  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [items, setItems] = useState<SkillStoreListing[]>([]);
  const [page, setPage] = useState(1);
  const [total, setTotal] = useState(0);
  const [groups, setGroups] = useState(EMPTY_SKILL_STORE_GROUPS);
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
    if (next !== "skills") params.delete("group");
    const search = params.toString();
    setSearchParams(search ? params : {}, { replace: true });
  };

  const setGroup = (next: SkillStoreGroup | null) => {
    const params = new URLSearchParams(searchParams);
    if (next) {
      params.set("kind", "skills");
      params.set("group", next);
    } else {
      params.delete("group");
    }
    const search = params.toString();
    setSearchParams(search ? params : {}, { replace: true });
  };

  const searching = Boolean(debouncedQ);
  const discover = !searching && kind == null && skillGroup == null;
  const showSkills = kind !== "workflows";
  const showWorkflows = kind !== "skills";
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
            row.group === g.id && !(discover && isOfficialAuthor(row.author)),
        ),
      })).filter((g) => g.rows.length > 0),
    [items, discover],
  );
  const visibleGroupChips = SKILL_STORE_GROUPS.filter((g) => groups[g.id] > 0);
  const visibleTemplates = useMemo(() => {
    const list = templates ?? [];
    if (!debouncedQ) return list;
    return list.filter((tpl) => templateMatches(tpl, debouncedQ));
  }, [templates, debouncedQ]);
  const workflowRail = discover
    ? visibleTemplates.slice(0, SHELF_CAP)
    : visibleTemplates;
  const workflowListingRail = discover ? wfItems.slice(0, SHELF_CAP) : wfItems;
  const featuredRail = discover ? featured.slice(0, SHELF_CAP) : [];
  const cta = selected ? installCta(selected) : null;
  const selectedCopy = selected ? listingCopy(selected) : null;
  const description =
    (open?.kind === "workflow" ? wfDetail?.description : detail?.description) ||
    selected?.description ||
    "";
  const showDescription =
    Boolean(description) && selectedCopy?.title !== description;
  const hasMore =
    (searching || skillGroup != null) &&
    items.length < total &&
    !loading &&
    !discover;
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
        <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0">
          <legend className="sr-only">货架种类</legend>
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
        </fieldset>
        {showSkills && !searching && visibleGroupChips.length > 0 ? (
          <fieldset className="m-0 flex flex-wrap gap-1.5 border-0 p-0">
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
      </div>

      {shelfError ? (
        <p className="mt-4 shrink-0 text-sm text-muted-foreground" role="alert">
          {shelfError}
        </p>
      ) : null}

      <div className="mt-6 min-h-0 flex-1 overflow-y-auto">
        {showWorkflows &&
        (kind === "workflows" || templatesHint || workflowRail.length > 0) ? (
          discover ? (
            workflowRail.length > 0 ? (
              <ShelfRail title="工作流 · 官方" action={seeAll("workflows")}>
                {workflowRail.map((tpl) => (
                  <WorkflowTile
                    key={tpl.id}
                    tpl={tpl}
                    onOpen={() => setUseTarget(tpl)}
                  />
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
                <p className="text-xs text-muted-foreground">{templatesHint}</p>
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
                  <StoreListingCard
                    key={row.id}
                    row={row}
                    colorVar={artifactColorVar("workflow")}
                    icon={Workflow}
                    onOpen={() => setOpen({ kind: "workflow", id: row.id })}
                  />
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
                        onOpen={() => setOpen({ kind: "workflow", id: row.id })}
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
                  <StoreListingCard
                    key={row.id}
                    row={row}
                    onOpen={() => setOpen({ kind: "skill", id: row.id })}
                  />
                ))}
              </ShelfRail>
            ) : null}

            {discover
              ? groupRails.map((g) => (
                  <ShelfRail
                    key={g.id}
                    title={g.label}
                    action={{
                      label: "查看全部",
                      onClick: () => setGroup(g.id),
                    }}
                  >
                    {g.rows.slice(0, SHELF_CAP).map((row) => (
                      <StoreListingCard
                        key={row.id}
                        row={row}
                        onOpen={() => setOpen({ kind: "skill", id: row.id })}
                      />
                    ))}
                  </ShelfRail>
                ))
              : null}

            {!discover &&
            showSkills &&
            (items.length > 0 ||
              loading ||
              kind === "skills" ||
              skillGroup != null) ? (
              <>
                {loading && items.length === 0 ? (
                  <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                    <Loader2 size={16} className="animate-spin" />
                    加载中…
                  </div>
                ) : null}
                {searching || skillGroup != null ? (
                  items.length > 0 ? (
                    <>
                      <SectionLabel>
                        {skillGroup
                          ? skillStoreGroupLabel(skillGroup)
                          : TOOLBOX_KIND_LABEL.skills}
                      </SectionLabel>
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
                  ) : null
                ) : (
                  groupRails.map((g) => (
                    <section key={g.id} className="mb-8">
                      <SectionLabel>{g.label}</SectionLabel>
                      <div className={SKILL_GRID_CLASS}>
                        {g.rows.map((row) => (
                          <StoreListingCard
                            key={row.id}
                            row={row}
                            onOpen={() =>
                              setOpen({ kind: "skill", id: row.id })
                            }
                          />
                        ))}
                      </div>
                    </section>
                  ))
                )}
              </>
            ) : null}

            {discover && loading && items.length === 0 ? (
              <div className="flex items-center justify-center gap-2 py-16 text-muted-foreground text-sm">
                <Loader2 size={16} className="animate-spin" />
                加载中…
              </div>
            ) : null}

            {skillsEmpty &&
            !searching &&
            (kind === "skills" || skillGroup != null) ? (
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

      <Dialog
        open={open !== null}
        onOpenChange={(next) => {
          if (!next && !reportOpen) setOpen(null);
        }}
      >
        <DialogContent
          size="lg"
          className="flex max-h-[min(80vh,36rem)] flex-col"
          data-testid={
            open?.kind === "workflow"
              ? "workflow-store-dialog"
              : "skill-store-dialog"
          }
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
                colorVar={artifactColorVar(
                  open?.kind === "workflow" ? "workflow" : "guidelines",
                )}
                size="lg"
              >
                {open?.kind === "workflow" ? (
                  <Workflow size={20} />
                ) : (
                  <Store size={20} />
                )}
              </CatalogIconShell>
              <div className="min-w-0">
                <DialogTitle>
                  {selectedCopy?.title ??
                    TOOLBOX_KIND_LABEL[
                      open?.kind === "workflow" ? "workflows" : "skills"
                    ]}
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
              <p className="text-sm text-foreground">{description}</p>
            ) : null}
            {detail?.content ? (
              <PromptDocument
                text={detail.content}
                compact={false}
                maxHeightClass="max-h-none"
              />
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
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy || !open}
              onClick={() => setReportOpen(true)}
            >
              举报
            </Button>
            {cta ? (
              <Button
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
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={reportOpen} onOpenChange={setReportOpen}>
        <DialogContent size="md">
          <DialogHeader>
            <DialogTitle>
              举报
              {open?.kind === "workflow"
                ? TOOLBOX_KIND_LABEL.workflows
                : TOOLBOX_KIND_LABEL.skills}
            </DialogTitle>
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
