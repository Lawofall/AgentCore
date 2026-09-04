import {
  brandPanelPrimary,
  confidenceLabel,
  confidencePill,
  statusPillInline,
  surfaceSubtle,
} from "@/components/ui/tone-presets";
import type {
  DebateBriefInfo,
  DebateHandoffInfo,
  DebateSideInfo,
} from "@/types/events";
import {
  Lightbulb,
  MessagesSquare,
  Scale,
  ShieldAlert,
  ShieldCheck,
  Swords,
  Target,
  UserRound,
  Users,
} from "lucide-react";
import type { ReactNode } from "react";
import { SideIdentity } from "../../SideChip";
import {
  FINDING_SEVERITY,
  FINDING_STATUS,
  findingDispositionLabel,
  gateLabel,
  sortFindings,
} from "../../findings";
import { type DebateForm, debateSideColorVar } from "../../model";
import {
  RISK_LEVELS,
  RISK_SEVERITY,
  type RiskItem,
  type RiskLevel,
  buildRiskItems,
  rankOf,
  riskCounts,
} from "../../severity";
import { ConsensusMap } from "../ConsensusMap";

/** 交接清单 kind；坏 kind 容错归 question（契约不变）。 */
type HandoffKind = "value" | "fact" | "question";

function asHandoffKind(raw: string): HandoffKind {
  return raw === "value" || raw === "fact" || raw === "question"
    ? raw
    : "question";
}

function briefHandoffs(brief: DebateBriefInfo): DebateHandoffInfo[] {
  return (brief.handoffs ?? []).map((h) => ({
    kind: asHandoffKind(h.kind),
    text: h.text,
  }));
}

export function BriefCard({
  brief,
  sides,
  form,
}: {
  brief: DebateBriefInfo;
  sides: DebateSideInfo[];
  form: DebateForm;
}) {
  if (form === "red_team") return <RedTeamBrief brief={brief} sides={sides} />;
  if (form === "roundtable") return <RoundtableBrief brief={brief} />;
  return <DebateBrief brief={brief} />;
}

/** 正反：① 裁决卡 → ② 留给你的（有交接则不展建议） */
function DebateBrief({ brief }: { brief: DebateBriefInfo }) {
  const handoffs = briefHandoffs(brief);
  const rec = handoffs.length === 0 ? brief.recommendation : undefined;
  return (
    <div className="space-y-4">
      <VerdictCard brief={brief} form="debate" />
      <YourCallZone handoffs={handoffs} recommendation={rec} form="debate" />
    </div>
  );
}

/** 红队同构：① 门决评定 → ② finding 台账（或旧 RiskBoard 降级）→ ③ 留给你的 */
function RedTeamBrief({
  brief,
  sides,
}: {
  brief: DebateBriefInfo;
  sides: DebateSideInfo[];
}) {
  const subject = sides.find((s) => s.is_subject) ?? null;
  const findings = brief.findings ?? [];
  const hasFindings = findings.length > 0;
  const risks = hasFindings ? [] : buildRiskItems(sides, brief);
  const defense = subject ? brief.strongest_points[subject.key] : undefined;
  const mustFix = brief.must_fix ?? [];
  return (
    <div className="space-y-4">
      <VerdictCard brief={brief} form="red_team" />
      {hasFindings ? (
        <BriefFindingBoard
          findings={findings}
          sides={sides}
          mustFix={mustFix}
        />
      ) : (
        <div className="space-y-3">
          <RiskBoard risks={risks} />
          {defense && subject && (
            <div>
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <ShieldCheck size={14} className="text-muted-foreground" />
                <span className="text-xs font-medium text-muted-foreground">
                  方案方回应
                </span>
                <SideIdentity
                  name={subject.name}
                  colorVar={debateSideColorVar(subject.key, subject.name)}
                  model={subject.model}
                />
              </div>
              <p className="text-sm text-foreground">{defense}</p>
            </div>
          )}
        </div>
      )}
      <YourCallZone
        handoffs={briefHandoffs(brief)}
        recommendation={brief.recommendation}
        form="red_team"
      />
    </div>
  );
}

/** 终审区 finding 摘要台账（结构字段；全文已在轮内英雄区）。 */
function BriefFindingBoard({
  findings,
  sides,
  mustFix,
}: {
  findings: NonNullable<DebateBriefInfo["findings"]>;
  sides: DebateSideInfo[];
  mustFix: string[];
}) {
  const nameByKey = new Map(sides.map((s) => [s.key, s.name]));
  const ordered = sortFindings(findings);
  const mustSet = new Set(mustFix);
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <h4 className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          <ShieldAlert size={14} />
          Finding 下场
        </h4>
        {mustFix.length > 0 && (
          <span className={statusPillInline.destructive}>
            must-fix {mustFix.length}
          </span>
        )}
      </div>
      <ul className="space-y-1.5">
        {ordered.map((f) => {
          const sev = FINDING_SEVERITY[f.severity];
          const st = FINDING_STATUS[f.status];
          const attacker = nameByKey.get(f.attacker_key) ?? f.attacker_key;
          const disposition = findingDispositionLabel(f.disposition ?? "");
          return (
            <li key={f.id} className={sev.surface}>
              <div className="flex flex-wrap items-center gap-1.5">
                <span className={sev.pill}>{sev.label}</span>
                <span className={st.pill}>{st.label}</span>
                {mustSet.has(f.id) && (
                  <span className={statusPillInline.destructive}>must-fix</span>
                )}
                <SideIdentity
                  name={attacker}
                  colorVar={debateSideColorVar(f.attacker_key, attacker)}
                />
              </div>
              <p className="mt-1 text-sm text-foreground">
                {f.target}
                {disposition ? (
                  <span className="text-muted-foreground">
                    {" "}
                    · 处置 {disposition}
                  </span>
                ) : null}
              </p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * ① 裁决卡（带边框）：纯判断区——结论倾向大字 + 置信；
 * 胜负手作卡内次级「理由」行；争点仅红队保留（正反不渲染）。
 * recommendation 已迁至 YourCallZone。
 */
function VerdictCard({
  brief,
  form,
}: {
  brief: DebateBriefInfo;
  form: "debate" | "red_team";
}) {
  const label = form === "red_team" ? "方案评定" : "结论倾向";
  const level = confidenceLevel(brief.confidence);
  const gate = form === "red_team" ? gateLabel(brief.gate) : null;
  const showCrux = form === "red_team" && !!brief.crux;
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          <Scale size={13} />
          {label}
        </span>
        <div className="flex shrink-0 items-center gap-1.5">
          {gate && <span className={statusPillInline.primary}>{gate}</span>}
          <span
            className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${confidencePill[level]}`}
          >
            置信 {confidenceLabel[level]}
          </span>
        </div>
      </div>
      <p className="mt-2 text-xl font-semibold leading-snug text-foreground">
        {brief.leaning}
      </p>
      {(brief.decisive || showCrux) && (
        <div className="mt-3 space-y-1.5 border-t border-border pt-3">
          {brief.decisive && (
            <ReasonRow
              icon={<Swords size={13} />}
              label={form === "red_team" ? "定门决" : "胜负手"}
            >
              {brief.decisive}
            </ReasonRow>
          )}
          {showCrux && (
            <ReasonRow icon={<Target size={13} />} label="争点">
              {brief.crux}
            </ReasonRow>
          )}
        </div>
      )}
    </div>
  );
}

function ReasonRow({
  icon,
  label,
  children,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
}) {
  return (
    <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>
        <span className="font-medium text-foreground">{label}</span>
        <span className="mx-1">·</span>
        {children}
      </span>
    </p>
  );
}

function RiskBoard({ risks }: { risks: RiskItem[] }) {
  if (risks.length === 0) return null;
  const counts = riskCounts(risks);
  const ordered = [...risks].sort((a, b) => rankOf(a.level) - rankOf(b.level));
  return (
    <div>
      <div className="mb-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <h4 className="flex items-center gap-1 text-xs font-medium text-muted-foreground">
          <ShieldAlert size={14} />
          风险清单
        </h4>
        <RiskTally counts={counts} />
      </div>
      <ul className="space-y-1.5">
        {ordered.map((r) => {
          const meta = r.level ? RISK_SEVERITY[r.level] : null;
          return (
            <li
              key={r.side.key}
              className={meta?.surface ?? "border-l-2 border-border pl-2.5"}
            >
              <div className="flex items-center justify-between gap-2">
                <SideIdentity
                  name={r.side.name}
                  colorVar={debateSideColorVar(r.side.key, r.side.name)}
                />
                {meta && <span className={meta.pill}>{meta.label}</span>}
              </div>
              <p className="mt-1 text-sm text-foreground">{r.text}</p>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function RiskTally({ counts }: { counts: Record<RiskLevel, number> }) {
  const shown = RISK_LEVELS.filter((l) => counts[l] > 0);
  if (shown.length === 0) return null;
  return (
    <div className="flex items-center gap-1">
      {shown.map((l) => (
        <span key={l} className={RISK_SEVERITY[l].pill}>
          {RISK_SEVERITY[l].label} {counts[l]}
        </span>
      ))}
    </div>
  );
}

/**
 * 圆桌：有 consensus_map → 共识/分歧地图；否则降级旧观点光谱。
 * 共同焦点 / 分歧由 RoundtableBrief → ③ 区处理。
 */
export function RoundtableSpectrum({
  brief,
  sides,
  subtopics,
}: {
  brief: DebateBriefInfo;
  sides: DebateSideInfo[];
  subtopics?: string[] | null;
}) {
  const map = brief.consensus_map ?? [];
  if (map.length > 0) {
    return (
      <ConsensusMap
        items={map}
        sides={sides}
        subtopics={subtopics}
        strongestPoints={brief.strongest_points}
        leaning={brief.leaning}
        recommendation={brief.recommendation}
      />
    );
  }
  return (
    <div className="space-y-3">
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <Users size={15} className="text-muted-foreground" />
        圆桌观点光谱
      </h3>
      <SidePointsGrid
        label="各视角核心主张"
        sides={sides}
        points={brief.strongest_points}
      />
      {brief.leaning && (
        <div className="rounded-lg border border-border/60 bg-muted/20 p-3">
          <h4 className="mb-1 flex items-center gap-1 text-xs font-medium text-muted-foreground">
            <MessagesSquare size={14} />
            综合观察
          </h4>
          <p className="text-sm text-foreground">{brief.leaning}</p>
          {brief.recommendation && (
            <p className="mt-1.5 flex items-start gap-1.5 text-sm text-muted-foreground">
              <Lightbulb size={14} className="mt-0.5 shrink-0" />
              <span>
                <span className="font-medium text-foreground">建议：</span>
                {brief.recommendation}
              </span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/** 圆桌 ③：共同焦点 + 留给你的 */
function RoundtableBrief({
  brief,
}: {
  brief: DebateBriefInfo;
}) {
  const handoffs = briefHandoffs(brief);
  if (!brief.crux && handoffs.length === 0) {
    return null;
  }
  return (
    <div className="space-y-4">
      {brief.crux && (
        <p className="flex items-start gap-1.5 text-sm text-foreground">
          <Target size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
          <span>
            <span className="font-medium">共同焦点：</span>
            {brief.crux}
          </span>
        </p>
      )}
      <YourCallZone handoffs={handoffs} />
    </div>
  );
}

/**
 * ③ 留给你的：顶部 AI 建议位，其后按 kind 三种形态——
 *   value → 问句卡置顶高光；
 *   fact → 还撑不牢的事实列表；
 *   question → 脚注一行收尾（不与前两者平级）。
 * 不挂关口按钮；收场后接着干走主输入框对 CEO 说话。
 * handoffs 全空但有 recommendation 时仍渲染面板。
 * 圆桌不传 recommendation（建议仍留在 RoundtableSpectrum「综合观察」）。
 */
function YourCallZone({
  handoffs,
  recommendation,
  form,
}: {
  handoffs: DebateHandoffInfo[];
  recommendation?: string;
  form?: "debate" | "red_team";
}) {
  const values = handoffs.filter((h) => asHandoffKind(h.kind) === "value");
  const facts = handoffs.filter((h) => asHandoffKind(h.kind) === "fact");
  const questions = handoffs.filter(
    (h) => asHandoffKind(h.kind) === "question",
  );
  const hasHandoffs =
    values.length > 0 || facts.length > 0 || questions.length > 0;
  if (!hasHandoffs && !recommendation) {
    return null;
  }

  const recLabel = form === "red_team" ? "加固建议" : "建议";

  return (
    <div className={brandPanelPrimary}>
      <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
        <UserRound size={15} className="text-primary" />
        留给你的
      </h3>
      {recommendation && (
        <p className="flex items-start gap-1.5 text-sm text-foreground">
          <Lightbulb
            size={14}
            className="mt-0.5 shrink-0 text-muted-foreground"
          />
          <span>
            <span className="font-medium">{recLabel}：</span>
            {recommendation}
          </span>
        </p>
      )}
      {values.length > 0 && (
        <div
          className={
            recommendation
              ? "space-y-2 border-t border-primary/15 pt-3"
              : "space-y-2"
          }
        >
          {values.map((it) => (
            <ValueCallCard key={it.text} text={it.text} />
          ))}
        </div>
      )}
      {facts.length > 0 && (
        <ul
          className={
            values.length > 0 || recommendation
              ? "space-y-2 border-t border-primary/15 pt-3"
              : "space-y-2"
          }
        >
          {facts.map((it) => (
            <li key={it.text} className="text-sm text-foreground">
              {it.text}
            </li>
          ))}
        </ul>
      )}
      {questions.length > 0 && (
        <p className="text-xs text-muted-foreground">
          只能等的：{questions.map((h) => h.text).join("；")}
        </p>
      )}
    </div>
  );
}

/** value：整场化简出的选择题——问句形态高光卡。
 *  问号兜底仅当末尾无终结标点（历史数据是「。」收尾的陈述句，别拼成「。？」）。 */
function ValueCallCard({ text }: { text: string }) {
  const questionMark = /[？?。！!…]$/.test(text) ? "" : "？";
  return (
    <div className={`rounded-lg border p-3 ${surfaceSubtle.primary}`}>
      <p className="text-base font-medium leading-snug text-foreground">
        {text}
        {questionMark ? (
          <span className="text-primary">{questionMark}</span>
        ) : null}
      </p>
    </div>
  );
}

/** 圆桌光谱等轻量网格（无比分条）。 */
function SidePointsGrid({
  label,
  sides,
  points,
}: {
  label: string;
  sides: DebateSideInfo[];
  points: Record<string, string>;
}) {
  return (
    <div>
      <h4 className="mb-1.5 text-xs font-medium text-muted-foreground">
        {label}
      </h4>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {sides.map((s) => {
          const colorVar = debateSideColorVar(s.key, s.name);
          return (
            <div key={s.key} className="border-l-2 border-border pl-2.5">
              <SideIdentity name={s.name} colorVar={colorVar} />
              <p className="mt-1 text-sm text-foreground">
                {points[s.key] ?? "—"}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

const CONFIDENCE_LEVELS = ["high", "medium", "low"] as const;
type ConfidenceLevel = (typeof CONFIDENCE_LEVELS)[number];

function confidenceLevel(raw: string): ConfidenceLevel {
  const s = raw.toLowerCase();
  if (CONFIDENCE_LEVELS.includes(s as ConfidenceLevel))
    return s as ConfidenceLevel;
  if (s.includes("high") || raw.includes("高")) return "high";
  if (s.includes("low") || raw.includes("低")) return "low";
  return "medium";
}
