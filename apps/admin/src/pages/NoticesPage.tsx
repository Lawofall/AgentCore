import { OpsTabs } from "@/components/SectionTabs";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
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
import { cn, fmtTime } from "@/lib/utils";
import { errorMessage } from "@/services/api";
import {
  NOTICE_TEMPLATES,
  buildFromSlots,
  emptySlotValues,
  surfacePublishHint,
  templateToFormSeed,
  type NoticeCardTemplate,
  type NoticeTemplate,
} from "@/lib/noticeTemplates";
import { useAdminListPage } from "@/hooks/useAdminListPage";
import { useFirstLoad } from "@/hooks/useFirstLoad";
import { oneOf, useUrlFilters } from "@/hooks/useUrlFilters";
import {
  type CreateNoticeRequest,
  type Notice,
  type NoticeDismissPolicy,
  type NoticeSeverity,
  type NoticeStatus,
  type NoticeSurface,
  type UpdateNoticeRequest,
  archiveNotice,
  createNotice,
  listNotices,
  publishNotice,
  updateNotice,
} from "@/services/adminNotices";
import { Archive, Copy, Megaphone, Pencil, Plus, RefreshCw, Send } from "lucide-react";
import {
  type FormEvent,
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
} from "react";
import { toast } from "sonner";

type Tone = "success" | "neutral" | "warning" | "destructive" | "primary";

const STATUS: Record<NoticeStatus, { label: string; tone: Tone }> = {
  draft: { label: "草稿", tone: "neutral" },
  published: { label: "已发布", tone: "success" },
  archived: { label: "已归档", tone: "warning" },
};

const SEVERITY: Record<NoticeSeverity, { label: string; tone: Tone }> = {
  critical: { label: "紧急", tone: "destructive" },
  high: { label: "重要", tone: "warning" },
  normal: { label: "普通", tone: "neutral" },
};

const SURFACE: Record<NoticeSurface, string> = {
  banner: "横幅",
  inbox: "IM 官方号",
  both: "横幅 + IM 官方号",
  modal: "弹窗 + IM 官方号",
};

const DISMISS: Record<NoticeDismissPolicy, string> = {
  once: "可关闭（不回潮）",
  never: "横幅可关、官方号常驻",
};

const PAGE_SIZE = 50;

type StatusFilter = NoticeStatus | "all";

const STATUS_FILTER_VALUES = [
  "all",
  "draft",
  "published",
  "archived",
] as const satisfies readonly StatusFilter[];

const STATUS_FILTERS: SelectOption[] = STATUS_FILTER_VALUES.map((value) => ({
  value,
  label: value === "all" ? "全部状态" : STATUS[value].label,
}));

/**
 * `status` is the API's own query field; 全部 is the default and stays out of the URL.
 * One list feeds both the dropdown and the codec, so a link can never carry a status
 * the page has no option for.
 */
const NOTICE_FILTERS = { status: oneOf(STATUS_FILTER_VALUES, "all") };

type FormState = {
  title: string;
  body: string;
  severity: NoticeSeverity;
  surface: NoticeSurface;
  dismiss_policy: NoticeDismissPolicy;
  card_template: NoticeCardTemplate;
  summary: string;
  cover_url: string;
  cta_label: string;
  cta_url: string;
  start_at: string;
  end_at: string;
};

const EMPTY_FORM: FormState = {
  title: "",
  body: "",
  severity: "normal",
  surface: "both",
  dismiss_policy: "once",
  card_template: "service",
  summary: "",
  cover_url: "",
  cta_label: "",
  cta_url: "",
  start_at: "",
  end_at: "",
};

/** ISO → `datetime-local` value in local timezone. */
function toLocalInput(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const p = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** `datetime-local` → ISO, or null when blank. */
function fromLocalInput(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const d = new Date(trimmed);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

function asCardTemplate(raw: string | null | undefined): NoticeCardTemplate {
  return raw === "article" ? "article" : "service";
}

function noticeToForm(n: Notice): FormState {
  return {
    title: n.title,
    body: n.body,
    severity: (n.severity as NoticeSeverity) || "normal",
    surface: (n.surface as NoticeSurface) || "both",
    dismiss_policy: (n.dismiss_policy as NoticeDismissPolicy) || "once",
    card_template: asCardTemplate(n.card_template),
    summary: n.summary ?? "",
    cover_url: n.cover_url ?? "",
    cta_label: n.cta_label ?? "",
    cta_url: n.cta_url ?? "",
    start_at: toLocalInput(n.start_at),
    end_at: toLocalInput(n.end_at),
  };
}

function buildCreateBody(form: FormState): CreateNoticeRequest {
  return {
    title: form.title.trim(),
    body: form.body.trim(),
    severity: form.severity,
    surface: form.surface,
    dismiss_policy: form.dismiss_policy,
    card_template: form.card_template,
    summary: form.summary.trim() || null,
    cover_url: form.cover_url.trim() || null,
    cta_label: form.cta_label.trim() || null,
    cta_url: form.cta_url.trim() || null,
    start_at: fromLocalInput(form.start_at),
    end_at: fromLocalInput(form.end_at),
  };
}

function buildUpdateBody(form: FormState): UpdateNoticeRequest {
  return {
    title: form.title.trim(),
    body: form.body.trim(),
    severity: form.severity,
    surface: form.surface,
    dismiss_policy: form.dismiss_policy,
    card_template: form.card_template,
    summary: form.summary.trim() || null,
    cover_url: form.cover_url.trim() || null,
    cta_label: form.cta_label.trim() || null,
    cta_url: form.cta_url.trim() || null,
    start_at: fromLocalInput(form.start_at),
    end_at: fromLocalInput(form.end_at),
  };
}

function asStatus(raw: string): NoticeStatus {
  if (raw === "draft" || raw === "published" || raw === "archived") return raw;
  return "draft";
}

function noticeSurface(n: Notice): NoticeSurface {
  return (n.surface as NoticeSurface) || "both";
}

function noticeDismiss(n: Notice): NoticeDismissPolicy {
  return (n.dismiss_policy as NoticeDismissPolicy) || "once";
}

/**
 * 正文的一行纯文本梗概，给列表的第二行用。
 *
 * 正文是 Markdown，只有客户端会渲染它；列表里直接放原文读到的是源码——「## 本次更新
 * - 桌面端 **0.9.14** 修复了…」。这里只剥语法，不渲染：一行预览不值一个 Markdown
 * 依赖，剥不干净最多多留一个符号，不会显示成别的内容。块级结构折成「·」分段，因为
 * 换行在这一行里本来就看不见。
 *
 * 下划线强调不剥：公告里 `snake_case` 的字段名远比 `_斜体_` 常见，剥了会吃掉标识符。
 */
function plainPreview(body: string): string {
  const inlined = body
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/`([^`]+)`/g, "$1");
  const blocks: string[] = [];
  for (const raw of inlined.split(/\r?\n/)) {
    const line = raw
      .replace(/^\s{0,3}(?:#{1,6}|>|[-*+]|\d+[.)])\s+/, "")
      .replace(/^\s*(?:[-*_]\s*){3,}$/, "")
      .trim();
    if (line) blocks.push(line);
  }
  return blocks
    .join(" · ")
    .replace(/\*\*([^*]+)\*\*/g, "$1")
    .replace(/\*([^*]+)\*/g, "$1");
}

/**
 * Everything that has to hold before a draft can be saved, in the order an operator
 * would notice it. Only 标题 / 正文 / 摘要 were checked before, so a notice could be
 * saved with a CTA button that had no link (dead button in the client), a cover path
 * the client can't resolve, or an end time before its start (never displays at all) —
 * all of which only surface once the thing is in front of users.
 */
function formError(form: FormState): string | null {
  if (!form.title.trim()) return "请填写标题";
  if (!form.body.trim()) return "请填写正文";
  if (form.card_template === "article" && !form.summary.trim()) {
    return "图文模板须填写摘要";
  }
  if (form.surface === "modal" && form.dismiss_policy === "never") {
    return "弹窗展示面仅支持「可关闭」策略";
  }
  const cover = form.cover_url.trim();
  if (cover && !/^https?:\/\//i.test(cover)) {
    return "封面 URL 需以 http:// 或 https:// 开头";
  }
  const ctaLabel = form.cta_label.trim();
  const ctaUrl = form.cta_url.trim();
  if (ctaLabel && !ctaUrl) return "填了 CTA 文案就要填链接，否则按钮点了没反应";
  if (ctaUrl && !ctaLabel) return "填了 CTA 链接就要填文案，否则按钮没有标题";
  if (ctaUrl && !/^(https?:\/\/|\/)/i.test(ctaUrl)) {
    return "CTA 链接需以 http(s):// 开头，或用「/」开头的应用内路径（如 /more/about）";
  }
  const start = fromLocalInput(form.start_at);
  const end = fromLocalInput(form.end_at);
  if (start && end && new Date(end) <= new Date(start)) {
    return "结束时间要晚于开始时间，否则公告永远不会展示";
  }
  return null;
}

type PendingAction = { kind: "publish" | "archive"; notice: Notice };

export function NoticesPage() {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useAdminListPage();
  const { values, set, reset } = useUrlFilters(NOTICE_FILTERS);
  const statusFilter = values.status;
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Notice | "new" | null>(null);
  /** 新建时的表单种子（模板 / 复制草稿）；编辑已有公告时忽略 */
  const [formSeed, setFormSeed] = useState<FormState | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingAction | null>(null);

  const openNew = (seed: FormState | null = null) => {
    setFormSeed(seed);
    setEditing("new");
  };

  const openEdit = (notice: Notice) => {
    setFormSeed(null);
    setEditing(notice);
  };

  const closeEditor = () => {
    setEditing(null);
    setFormSeed(null);
  };

  const copyAsDraft = (notice: Notice) => {
    openNew({
      ...noticeToForm(notice),
      start_at: "",
      end_at: "",
    });
  };

  // Status filter + page change can overlap; only the latest response wins.
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
      const res = await listNotices(
        {
          status: statusFilter === "all" ? undefined : statusFilter,
          limit: PAGE_SIZE,
          offset: (page - 1) * PAGE_SIZE,
        },
        ac.signal,
      );
      if (ac.signal.aborted || gen !== loadGenRef.current) return;
      setNotices(res.data);
      setTotal(res.total);
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

  const upsertLocal = (updated: Notice) => {
    setNotices((prev) => {
      const idx = prev.findIndex((n) => n.id === updated.id);
      if (idx < 0) return [updated, ...prev];
      const next = [...prev];
      next[idx] = updated;
      return next;
    });
  };

  const runPending = async (act: PendingAction) => {
    if (busyId) return;
    const { kind, notice } = act;
    setBusyId(notice.id);
    try {
      const updated =
        kind === "publish"
          ? await publishNotice(notice.id)
          : await archiveNotice(notice.id);
      upsertLocal(updated);
      toast.success(kind === "publish" ? "公告已发布" : "公告已归档");
      const landsOutsideFilter =
        statusFilter !== "all" &&
        statusFilter !== (kind === "publish" ? "published" : "archived");
      if (landsOutsideFilter) void load();
    } catch (err) {
      toast.error(errorMessage(err));
    } finally {
      setBusyId(null);
      setPending(null);
    }
  };

  const onSaved = (saved: Notice, isNew: boolean) => {
    closeEditor();
    if (isNew) {
      toast.success("草稿已创建");
      void load();
    } else {
      toast.success("公告已更新");
      upsertLocal(saved);
    }
  };

  const filtered = statusFilter !== "all";
  const firstLoad = loading && notices.length === 0 && !error;
  const freezeFilters = useFirstLoad(loading);
  const outOfRange = notices.length === 0 && total > 0 && page > 1;

  return (
    <Page>
      <PageHeader
        title="运营"
        note="发布与归档立即对用户生效；已投递的 IM 消息不会被撤回"
        actions={
          <>
            <OpsTabs />
            <Button size="sm" onClick={() => openNew()}>
              <Plus size={14} />
              新建公告
            </Button>
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
        filters={
          <Select
            aria-label="按状态筛选"
            value={statusFilter}
            disabled={freezeFilters}
            onChange={(e) => set({ status: e.target.value as StatusFilter })}
            options={STATUS_FILTERS}
          />
        }
      />

      {firstLoad ? (
        <TableSkeleton columns={7} />
      ) : error ? (
        <ErrorState message={error} onRetry={() => void load()} />
      ) : (
        <Refreshing active={loading}>
          {notices.length === 0 ? (
            <Card>
              {outOfRange ? (
                // 共 N 条 next to “还没有公告” reads as data loss; it's just a stale
                // `?page=` from a bookmark or a back step.
                <EmptyState
                  icon={Megaphone}
                  title="这一页没有公告"
                  description={`当前共 ${total} 条，第 ${page} 页已超出范围。`}
                  action={
                    <Button variant="outline" size="sm" onClick={() => setPage(1)}>
                      回到第一页
                    </Button>
                  }
                />
              ) : (
                <EmptyState
                  icon={Megaphone}
                  title={
                    filtered
                      ? `没有「${STATUS[statusFilter].label}」状态的公告`
                      : "还没有公告"
                  }
                  description={
                    filtered
                      ? "换个状态再看，或清除筛选查看全部公告。"
                      : "新建后先存为草稿，确认文案与展示面再发布。"
                  }
                  action={
                    filtered ? (
                      <Button variant="outline" size="sm" onClick={reset}>
                        清除筛选
                      </Button>
                    ) : (
                      <Button size="sm" onClick={() => openNew()}>
                        <Plus size={14} />
                        新建公告
                      </Button>
                    )
                  }
                />
              )}
            </Card>
          ) : (
            <TableFrame minWidth={1040}>
              <THead>
                <Th>标题</Th>
                <Th>状态</Th>
                <Th>级别</Th>
                <Th>展示面</Th>
                <Th>关闭策略</Th>
                <Th>更新时间</Th>
                <Th align="right">操作</Th>
              </THead>
              <tbody>
                {notices.map((n) => {
                  const status = asStatus(n.status);
                  const s = STATUS[status];
                  const sev =
                    SEVERITY[(n.severity as NoticeSeverity) || "normal"] ??
                    SEVERITY.normal;
                  const surface = SURFACE[noticeSurface(n)] ?? n.surface;
                  const dismiss = DISMISS[noticeDismiss(n)] ?? n.dismiss_policy;
                  const rowBusy = busyId === n.id;
                  const anyBusy = busyId !== null;
                  const editable = status !== "archived";
                  return (
                    <TableRow key={n.id}>
                      <Td>
                        <div className="font-medium text-foreground">{n.title}</div>
                        <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                          {plainPreview(n.body)}
                        </div>
                      </Td>
                      <Td>
                        <Badge tone={s.tone}>{s.label}</Badge>
                      </Td>
                      <Td>
                        <Badge tone={sev.tone}>{sev.label}</Badge>
                      </Td>
                      <Td className="text-muted-foreground">{surface}</Td>
                      <Td className="text-muted-foreground">{dismiss}</Td>
                      <Td className="whitespace-nowrap tabular-nums text-muted-foreground">
                        {fmtTime(n.updated_at)}
                      </Td>
                      <Td align="right">
                        <div className="flex items-center justify-end gap-1">
                          {editable && (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={anyBusy}
                              onClick={() => openEdit(n)}
                            >
                              <Pencil size={14} />
                              编辑
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            disabled={anyBusy}
                            onClick={() => copyAsDraft(n)}
                            title="复制字段为新草稿"
                          >
                            <Copy size={14} />
                            复制
                          </Button>
                          {status === "draft" && (
                            <Button
                              variant="ghost"
                              size="sm"
                              disabled={anyBusy}
                              onClick={() => setPending({ kind: "publish", notice: n })}
                            >
                              {rowBusy ? <Spinner /> : <Send size={14} />}
                              发布
                            </Button>
                          )}
                          {status !== "archived" && (
                            <Button
                              variant="ghost"
                              size="sm"
                              className="text-destructive"
                              disabled={anyBusy}
                              onClick={() => setPending({ kind: "archive", notice: n })}
                            >
                              {rowBusy ? <Spinner /> : <Archive size={14} />}
                              归档
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
            total={total}
            onPageChange={setPage}
            disabled={loading}
          />
        </Refreshing>
      )}

      {editing && (
        <NoticeFormDialog
          notice={editing === "new" ? null : editing}
          seed={editing === "new" ? formSeed : null}
          onClose={closeEditor}
          onSaved={onSaved}
        />
      )}

      {pending && (
        <ConfirmActionDialog
          action={pending}
          busy={busyId === pending.notice.id}
          onClose={() => setPending(null)}
          onConfirm={() => void runPending(pending)}
        />
      )}
    </Page>
  );
}

/**
 * 发布 / 归档 both reach every user immediately and neither is undoable from this
 * console (归档 also locks editing — the API rejects writes to archived notices), so
 * they no longer fire straight off a single ghost-button click.
 */
function ConfirmActionDialog({
  action,
  busy,
  onClose,
  onConfirm,
}: {
  action: PendingAction;
  busy: boolean;
  onClose: () => void;
  onConfirm: () => void;
}) {
  const { kind, notice } = action;
  const publishing = kind === "publish";
  const hint = surfacePublishHint(noticeSurface(notice), noticeDismiss(notice));

  return (
    <Dialog
      open
      onClose={onClose}
      busy={busy}
      title={publishing ? "发布公告" : "归档公告"}
      description={notice.title}
      footer={
        <>
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button
            variant={publishing ? "primary" : "destructive"}
            size="sm"
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? <Spinner /> : publishing ? <Send size={14} /> : <Archive size={14} />}
            {publishing ? "确认发布" : "确认归档"}
          </Button>
        </>
      }
    >
      <p className="text-sm text-muted-foreground">
        {publishing
          ? "发布后立即对所有用户生效。已投递到 IM「AgentCore 官方」的消息无法撤回；后续归档只能停止横幅 / 弹窗展示。"
          : "归档后从横幅 / 弹窗撤下，并且不能再编辑（服务端会拒绝对已归档公告的修改）。已投递的 IM 消息不受影响。"}
      </p>
      {publishing && hint && (
        <p className="mt-3 rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
          {hint}
        </p>
      )}
    </Dialog>
  );
}

function NoticeFormDialog({
  notice,
  seed,
  onClose,
  onSaved,
}: {
  notice: Notice | null;
  seed: FormState | null;
  onClose: () => void;
  onSaved: (saved: Notice, isNew: boolean) => void;
}) {
  const isNew = notice === null;
  const formId = useId();
  const [form, setForm] = useState<FormState>(() => {
    if (notice) return noticeToForm(notice);
    if (seed) return seed;
    return EMPTY_FORM;
  });
  const [activeTemplateId, setActiveTemplateId] = useState<string | null>(null);
  const [slotValues, setSlotValues] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);

  const activeTemplate = NOTICE_TEMPLATES.find((t) => t.id === activeTemplateId);
  const invalidModalNever =
    form.surface === "modal" && form.dismiss_policy === "never";

  const set =
    <K extends keyof FormState>(key: K) =>
    (value: FormState[K]) =>
      setForm((prev) => ({ ...prev, [key]: value }));

  const applyTemplate = (t: NoticeTemplate) => {
    setForm(templateToFormSeed(t));
    setActiveTemplateId(t.id);
    setSlotValues(emptySlotValues(t));
  };

  const applySlotsToCopy = () => {
    if (!activeTemplate) return;
    const built = buildFromSlots(activeTemplate, slotValues);
    setForm((prev) => ({
      ...prev,
      title: built.title,
      body: built.body,
      ...(built.summary !== undefined ? { summary: built.summary } : {}),
    }));
    toast.success("已根据快捷填写生成标题与正文");
  };

  const publishHint = useMemo(
    () => surfacePublishHint(form.surface, form.dismiss_policy),
    [form.surface, form.dismiss_policy],
  );

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    const problem = formError(form);
    if (problem) {
      toast.error(problem);
      return;
    }
    setSaving(true);
    try {
      if (isNew) {
        const created = await createNotice(buildCreateBody(form));
        onSaved(created, true);
      } else {
        const updated = await updateNotice(notice.id, buildUpdateBody(form));
        onSaved(updated, false);
      }
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  };

  const fieldClass =
    "w-full rounded-lg border border-input bg-card px-3 py-2 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

  return (
    <Dialog
      open
      onClose={onClose}
      busy={saving}
      size="lg"
      title={isNew ? "新建公告" : "编辑公告"}
      description={
        isNew
          ? "可先套用模板，用快捷填写生成正文后再微调；创建后为草稿"
          : "已归档不可改；已发布内容修改后立即对用户生效（不回填历史 IM）"
      }
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={saving}
          >
            取消
          </Button>
          <Button type="submit" form={formId} size="sm" disabled={saving}>
            {saving && <Spinner />}
            {isNew ? "创建草稿" : "保存"}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        onSubmit={(e) => void handleSubmit(e)}
        className="flex flex-col gap-4"
      >
        {isNew && (
          <div className="flex flex-col gap-2">
            <span className="text-xs font-medium text-muted-foreground">套用模板</span>
            <div className="flex flex-wrap gap-1.5">
              {NOTICE_TEMPLATES.map((t) => {
                const selected = activeTemplateId === t.id;
                return (
                  <button
                    key={t.id}
                    type="button"
                    title={t.description}
                    onClick={() => applyTemplate(t)}
                    className={cn(
                      "rounded-lg border px-2.5 py-1 text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
                      selected
                        ? "border-primary bg-primary/10 text-foreground"
                        : "border-border bg-card text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                    )}
                  >
                    {t.label}
                  </button>
                );
              })}
            </div>
            {activeTemplate?.endHint && (
              <p className="text-xs text-warning">{activeTemplate.endHint}</p>
            )}
            {activeTemplate && activeTemplate.slots.length > 0 && (
              <div className="rounded-lg border border-border bg-muted/30 p-3">
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="text-xs font-medium text-muted-foreground">
                    快捷填写（填完点生成，可再手改标题/正文）
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    onClick={applySlotsToCopy}
                  >
                    生成正文
                  </Button>
                </div>
                <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                  {activeTemplate.slots.map((slot) => (
                    <label
                      key={slot.key}
                      className={cn(
                        "flex flex-col gap-1",
                        slot.multiline && "sm:col-span-2",
                      )}
                    >
                      <span className="text-xs font-medium text-muted-foreground">
                        {slot.label}
                      </span>
                      {slot.multiline ? (
                        <textarea
                          value={slotValues[slot.key] ?? ""}
                          onChange={(e) =>
                            setSlotValues((prev) => ({
                              ...prev,
                              [slot.key]: e.target.value,
                            }))
                          }
                          placeholder={slot.placeholder}
                          rows={2}
                          className={fieldClass}
                        />
                      ) : (
                        <Input
                          value={slotValues[slot.key] ?? ""}
                          onChange={(e) =>
                            setSlotValues((prev) => ({
                              ...prev,
                              [slot.key]: e.target.value,
                            }))
                          }
                          placeholder={slot.placeholder}
                        />
                      )}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">标题</span>
          <Input
            value={form.title}
            onChange={(e) => set("title")(e.target.value)}
            placeholder="简短标题"
            autoFocus
          />
        </label>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">正文</span>
          <textarea
            value={form.body}
            onChange={(e) => set("body")(e.target.value)}
            placeholder="公告正文"
            rows={7}
            className={fieldClass}
          />
        </label>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">官方号模板</span>
            <Select
              aria-label="官方号模板"
              value={form.card_template}
              onChange={(e) =>
                set("card_template")(e.target.value as NoticeCardTemplate)
              }
              options={[
                { value: "service", label: "服务通知（默认）" },
                { value: "article", label: "图文（须摘要）" },
              ]}
              className="w-full"
            />
          </label>
          <label className="flex flex-col gap-1.5 sm:col-span-2">
            <span className="text-xs font-medium text-muted-foreground">
              摘要{form.card_template === "article" ? "（图文必填）" : "（可选）"}
            </span>
            <Input
              value={form.summary}
              onChange={(e) => set("summary")(e.target.value)}
              placeholder={
                form.card_template === "article"
                  ? "卡面摘要，两句内"
                  : "服务卡可空，卡面用正文"
              }
            />
          </label>
        </div>

        <label className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-muted-foreground">
            封面 URL（可选）
          </span>
          <Input
            value={form.cover_url}
            onChange={(e) => set("cover_url")(e.target.value)}
            placeholder="https://… · 无图勿填占位"
          />
        </label>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">级别</span>
            <Select
              aria-label="级别"
              value={form.severity}
              onChange={(e) => set("severity")(e.target.value as NoticeSeverity)}
              options={[
                { value: "normal", label: "普通" },
                { value: "high", label: "重要" },
                { value: "critical", label: "紧急" },
              ]}
              className="w-full"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">展示面</span>
            <Select
              aria-label="展示面"
              value={form.surface}
              onChange={(e) => set("surface")(e.target.value as NoticeSurface)}
              options={[
                { value: "both", label: "横幅 + IM 官方号" },
                { value: "modal", label: "弹窗 + IM 官方号" },
                { value: "banner", label: "仅横幅" },
                { value: "inbox", label: "仅 IM 官方号" },
              ]}
              className="w-full"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">关闭策略</span>
            <Select
              aria-label="关闭策略"
              value={form.dismiss_policy}
              onChange={(e) =>
                set("dismiss_policy")(e.target.value as NoticeDismissPolicy)
              }
              options={[
                { value: "once", label: "可关闭（不回潮）" },
                { value: "never", label: "横幅可关、官方号常驻" },
              ]}
              className="w-full"
            />
          </label>
        </div>

        {publishHint && (
          <p
            className={cn(
              "rounded-lg border px-3 py-2 text-xs",
              invalidModalNever
                ? "border-destructive/40 bg-destructive/10 text-destructive"
                : "border-border bg-muted/40 text-muted-foreground",
            )}
          >
            {publishHint}
          </p>
        )}

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">CTA 文案</span>
            <Input
              value={form.cta_label}
              onChange={(e) => set("cta_label")(e.target.value)}
              placeholder="可选，如「了解更多」"
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">CTA 链接</span>
            <Input
              value={form.cta_url}
              onChange={(e) => set("cta_url")(e.target.value)}
              placeholder="https://… 或应用内 /more/about"
            />
          </label>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">开始时间</span>
            <Input
              type="datetime-local"
              value={form.start_at}
              onChange={(e) => set("start_at")(e.target.value)}
            />
          </label>
          <label className="flex flex-col gap-1.5">
            <span className="text-xs font-medium text-muted-foreground">结束时间</span>
            <Input
              type="datetime-local"
              value={form.end_at}
              onChange={(e) => set("end_at")(e.target.value)}
            />
          </label>
        </div>
      </form>
    </Dialog>
  );
}
