import type { WorkflowTemplate } from "@/services/workflows";

/** 官方 playbook id → 「什么时候该挑它」。标题一律取接口返回值，这里只写目标。 */
const PICK_WHEN: Record<string, string> = {
  parallel_brief: "只想弄懂议题、先摸清几个方向",
  research_report: "要一份能落盘交付的长文报告",
  build_app: "从零搭一个能跑的应用",
  compare_options: "要在几个选项里比完再拍板",
};

/**
 * 官方模板区的选型提示：只讲「什么目标挑哪个」。
 *
 * 条目与标题都来自接口返回的目录，目录里没有的模板不会被点名——此前是硬编码
 * 文案，点过并不在目录里的模板名。
 */
export function OfficialTemplateGuide({
  templates,
}: {
  templates: WorkflowTemplate[];
}) {
  const rows = templates.flatMap((t) => {
    const when = PICK_WHEN[t.id];
    return when ? [{ id: t.id, title: t.title, when }] : [];
  });
  if (rows.length === 0) return null;

  return (
    <div
      data-testid="official-template-guide"
      className="rounded-lg border border-border bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground"
    >
      <p className="font-medium text-foreground">选模板先看目标</p>
      <ul className="mt-1 space-y-0.5">
        {rows.map((r) => (
          <li key={r.id}>
            {r.when} → 「{r.title}」
          </li>
        ))}
      </ul>
    </div>
  );
}
