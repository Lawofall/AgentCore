import { SearchField } from "@/components/ui";
import {
  ArrowLeft,
  ChevronDown,
  ChevronRight,
  FlaskConical,
  PanelLeftClose,
  PanelLeftOpen,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

interface Entry {
  name: string;
  description: string;
}

interface ScenarioListProps {
  /** Committed conformance vectors, grouped by AI-state family. */
  fixtures: Entry[];
  selected: string | null;
  onSelect: (name: string) => void;
}

// Fixture names are family-prefixed (`single_agent_*`, `multi_agent_*`, …). The
// prefixes are multi-token, so families are matched against this fixed table
// (longest match isn't needed — the ids don't overlap) rather than split on `_`.
// Display order is this array, with anything unmatched bucketed last under 其他.
const FAMILIES: { id: string; label: string }[] = [
  { id: "single_agent", label: "单 Agent" },
  { id: "multi_agent", label: "多 Agent" },
  { id: "approval", label: "审批" },
  { id: "plan_review", label: "计划复核" },
  { id: "board_ops", label: "白板操作" },
];

const OTHER = { id: "other", label: "其他" };

function familyOf(name: string): { id: string; label: string } {
  return (
    FAMILIES.find((f) => name === f.id || name.startsWith(`${f.id}_`)) ?? OTHER
  );
}

// 多 Agent 有 25 个场景，再按主题切二级分组。匹配 `multi_agent_` 之后的语义前缀；
// 未命中的长尾落到「其他」。展示顺序即此数组顺序。
const MULTI_SUBS: { id: string; label: string }[] = [
  { id: "debate", label: "辩论 / 对抗" },
  { id: "roundtable", label: "圆桌" },
  { id: "escalate", label: "升级" },
  { id: "legal", label: "法律战情室" },
  { id: "delegate", label: "派单 / 子计划" },
  { id: "worker", label: "队员产物" },
  { id: "revise", label: "改稿 / 重规划" },
  { id: "context", label: "上下文" },
  { id: "other", label: "其他" },
];

function multiSubOf(name: string): string {
  const s = name.slice("multi_agent_".length);
  if (s.startsWith("debate") || s.startsWith("red_team")) return "debate";
  if (s.startsWith("roundtable")) return "roundtable";
  if (s.startsWith("blocking_escalate") || s.startsWith("escalation"))
    return "escalate";
  if (s.startsWith("legal")) return "legal";
  if (s.startsWith("delegate") || s.startsWith("lead_subplan"))
    return "delegate";
  if (s.startsWith("worker")) return "worker";
  if (s.startsWith("plan_revised") || s.startsWith("revision")) return "revise";
  if (s.startsWith("captain_context") || s.startsWith("received_context"))
    return "context";
  return "other";
}

interface SubGroup {
  id: string;
  label: string;
  items: Entry[];
}
interface Group {
  id: string;
  label: string;
  items: Entry[];
  /** Present only for families that split into a second level (多 Agent). */
  subs?: SubGroup[];
}

/**
 * The preview's scenario navigator: a live filter box + family-grouped fixture
 * list, and a one-click collapse to hand the full window width to the replayed
 * surface — the canvas view especially. All UI state here is ephemeral/human-only
 * (search text, open groups, collapsed); the canonical selection stays URL-driven
 * in PreviewPage so the shoot harness and deep links are unaffected.
 */
export function ScenarioList({
  fixtures,
  selected,
  onSelect,
}: ScenarioListProps) {
  const [collapsed, setCollapsed] = useState(false);
  const [query, setQuery] = useState("");
  const [toggled, setToggled] = useState<Set<string>>(new Set());
  const selectedRef = useRef<HTMLLIElement>(null);

  const total = fixtures.length;

  // Reveal the selected scenario on select / deep-link: open its family + (多 Agent)
  // sub-group if collapsed, then scroll it into view (it already reads selected via
  // bg-accent). Manual collapse afterwards is respected until selection changes. The
  // rAF defers the scroll until after the expand re-render commits, so the row exists
  // when we scroll to it.
  useEffect(() => {
    if (!selected) return;
    if (fixtures.some((f) => f.name === selected)) {
      const famId = familyOf(selected).id;
      const subId =
        famId === "multi_agent" ? `multi_agent/${multiSubOf(selected)}` : null;
      setToggled((prev) => {
        const next = new Set(prev);
        next.delete(famId);
        if (subId) next.add(subId);
        return next;
      });
    }
    const raf = requestAnimationFrame(() =>
      selectedRef.current?.scrollIntoView({ block: "nearest" }),
    );
    return () => cancelAnimationFrame(raf);
  }, [selected, fixtures]);

  if (collapsed) {
    return (
      <aside className="flex w-10 shrink-0 flex-col items-center gap-3 border-r border-border py-3">
        <Link
          to="/"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="退出预览"
          title="退出预览"
        >
          <ArrowLeft size={16} />
        </Link>
        <button
          type="button"
          onClick={() => setCollapsed(false)}
          aria-label="展开场景列表"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <PanelLeftOpen size={16} />
        </button>
        <FlaskConical size={16} className="text-primary" />
      </aside>
    );
  }

  const q = query.trim().toLowerCase();
  const matches = (e: Entry) =>
    !q ||
    e.name.toLowerCase().includes(q) ||
    e.description.toLowerCase().includes(q);

  const filteredFixtures = fixtures.filter(matches);

  const groups: Group[] = [...FAMILIES, OTHER]
    .map((f): Group => {
      const items = filteredFixtures.filter(
        (fx) => familyOf(fx.name).id === f.id,
      );
      if (f.id !== "multi_agent") return { ...f, items };
      const subs = MULTI_SUBS.map((sub) => ({
        ...sub,
        items: items.filter((fx) => multiSubOf(fx.name) === sub.id),
      })).filter((sub) => sub.items.length > 0);
      return { ...f, items, subs };
    })
    .filter((g) => g.items.length > 0);

  // Top families default open; the 多 Agent second-level themes (id contains "/")
  // default collapsed so that section reads as just its ~10 theme headers until a
  // theme is clicked open. `toggled` holds the ids flipped away from their default.
  // While searching, force everything open so a match is never hidden.
  const defaultOpen = (id: string) => !id.includes("/");
  const isOpen = (id: string) =>
    q.length > 0 || (defaultOpen(id) ? !toggled.has(id) : toggled.has(id));
  const toggleGroup = (id: string) =>
    setToggled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const itemButton = (e: Entry) => (
    <button
      type="button"
      onClick={() => onSelect(e.name)}
      className={`w-full rounded-lg px-3 py-2 text-left ${
        selected === e.name
          ? "bg-accent text-foreground"
          : "text-muted-foreground hover:bg-accent hover:text-foreground"
      }`}
    >
      <span className="block truncate text-sm font-medium">{e.name}</span>
      <span className="mt-0.5 block truncate text-xs text-muted-foreground">
        {e.description}
      </span>
    </button>
  );

  // Collapsible section header, reused for top families and the 多 Agent
  // second-level themes (indented).
  const groupHeader = (
    id: string,
    label: string,
    count: number,
    sub: boolean,
  ) => (
    <button
      type="button"
      onClick={() => toggleGroup(id)}
      aria-expanded={isOpen(id)}
      className={`flex w-full items-center gap-1 rounded-lg py-1 text-xs font-medium text-muted-foreground hover:text-foreground ${
        sub ? "pl-5 pr-2" : "px-2"
      }`}
    >
      {isOpen(id) ? (
        <ChevronDown size={14} className="shrink-0" />
      ) : (
        <ChevronRight size={14} className="shrink-0" />
      )}
      <span className="flex-1 truncate text-left">{label}</span>
      <span className="tabular-nums text-muted-foreground/70">{count}</span>
    </button>
  );

  const itemList = (items: Entry[], indented: boolean) => (
    <ul className={`space-y-0.5 ${indented ? "pl-3" : ""}`}>
      {items.map((fx) => (
        <li key={fx.name} ref={fx.name === selected ? selectedRef : undefined}>
          {itemButton(fx)}
        </li>
      ))}
    </ul>
  );

  return (
    <aside className="flex w-72 shrink-0 flex-col border-r border-border">
      <div className="flex items-center gap-2 border-b border-border px-4 py-3">
        <Link
          to="/"
          className="shrink-0 rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
          aria-label="退出预览"
          title="退出预览"
        >
          <ArrowLeft size={16} />
        </Link>
        <FlaskConical size={18} className="shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <h1 className="truncate text-base font-semibold text-foreground">
            前端预览
          </h1>
          <p className="text-xs text-muted-foreground">
            {total} 个场景 · 离线回放
          </p>
        </div>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          aria-label="收起场景列表"
          className="rounded-lg p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground"
        >
          <PanelLeftClose size={16} />
        </button>
      </div>

      <div className="border-b border-border px-3 py-2">
        <SearchField
          value={query}
          onValueChange={setQuery}
          placeholder="筛选场景…"
          aria-label="筛选场景"
        />
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {total === 0 ? (
          <p className="px-2 py-4 text-xs text-muted-foreground">
            未找到场景。请确认 packages/protocol-conformance/fixtures
            存在，并运行 `uv run python -m agentcore.conformance.export`
            生成向量。
          </p>
        ) : groups.length === 0 ? (
          <p className="px-2 py-4 text-xs text-muted-foreground">
            无匹配场景「{query}」。
          </p>
        ) : (
          groups.map((g) => (
            <div key={g.id} className="mb-1">
              {groupHeader(g.id, g.label, g.items.length, false)}
              {isOpen(g.id) &&
                (g.subs
                  ? g.subs.map((sub) => (
                      <div key={sub.id}>
                        {groupHeader(
                          `${g.id}/${sub.id}`,
                          sub.label,
                          sub.items.length,
                          true,
                        )}
                        {isOpen(`${g.id}/${sub.id}`) &&
                          itemList(sub.items, true)}
                      </div>
                    ))
                  : itemList(g.items, false))}
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
