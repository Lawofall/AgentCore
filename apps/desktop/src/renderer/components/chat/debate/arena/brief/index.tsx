import { Card, SectionLabel } from "@/components/ui";
import {
  confidenceLabel,
  confidencePill,
  statusPillInline,
} from "@/components/ui/tone-presets";
import { cn } from "@/lib/utils";
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
import {
  type StanceSide,
  splitFactDisplay,
  splitLeaning,
  splitValueCall,
} from "./split";

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
  return <DebateBrief brief={brief} sides={sides} />;
}

/** 正反：同一套 Card——裁决 + 交接，不再分蓝底/白底两壳。 */
function DebateBrief({
  brief,
  sides,
}: {
  brief: DebateBriefInfo;
  sides: DebateSideInfo[];
}) {
  const handoffs = briefHandoffs(brief);
  const rec = handoffs.length === 0 ? brief.recommendation : undefined;
  return (
    <Card className="p-4">
      <VerdictCard brief={brief} form="debate" sides={sides} />
      <YourCallZone
        divided
        handoffs={handoffs}
        recommendation={rec}
        form="debate"
      />
    </Card>
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
      <Card className="p-4">
        <VerdictCard brief={brief} form="red_team" sides={sides} />
      </Card>
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
        shell="card"
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
 * 裁决区：站队徽章 + 命题 + 反转次行 + 胜负手。外壳由父级 Card 提供。
 */
function VerdictCard({
  brief,
  form,
  sides,
}: {
  brief: DebateBriefInfo;
  form: "debate" | "red_team";
  sides?: DebateSideInfo[];
}) {
  const label = form === "red_team" ? "方案评定" : "结论倾向";
  const level = confidenceLevel(brief.confidence);
  const gate = form === "red_team" ? gateLabel(brief.gate) : null;
  const showCrux = form === "red_team" && !!brief.crux;
  const { stanceLabel, stanceSide, thesis, reversal } = splitLeaning(
    brief.leaning,
    sides,
  );
  const heading = thesis || stanceLabel || brief.leaning;
  const showStancePill = Boolean(stanceLabel && thesis);
  return (
    <div>
      <div className="flex items-center justify-between gap-2">
        <SectionLabel className="flex items-center gap-1">
          <Scale size={13} />
          {label}
        </SectionLabel>
        <div className="flex shrink-0 items-center gap-1.5">
          {showStancePill && stanceLabel ? (
            <StancePill label={stanceLabel} side={stanceSide} />
          ) : null}
          {gate && <span className={statusPillInline.primary}>{gate}</span>}
          <span
            className={`rounded-full px-1.5 py-0.5 text-xs font-medium ${confidencePill[level]}`}
          >
            置信 {confidenceLabel[level]}
          </span>
        </div>
      </div>
      {heading ? (
        <p
          className="mt-2 text-base font-semibold leading-snug text-foreground"
          style={
            !showStancePill && stanceSide
              ? {
                  color:
                    stanceSide === "pro"
                      ? "var(--debate-side-pro)"
                      : "var(--debate-side-con)",
                }
              : undefined
          }
        >
          {heading}
        </p>
      ) : null}
      {reversal ? (
        <p className="mt-1.5 text-sm text-muted-foreground">{reversal}</p>
      ) : null}
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

function StancePill({
  label,
  side,
}: {
  label: string;
  side: StanceSide;
}) {
  const colorVar =
    side === "pro"
      ? "var(--debate-side-pro)"
      : side === "con"
        ? "var(--debate-side-con)"
        : null;
  return (
    <span
      className={cn(
        "rounded-full px-1.5 py-0.5 text-xs font-medium",
        !colorVar && statusPillInline.muted,
      )}
      style={
        colorVar
          ? {
              color: colorVar,
              background: `color-mix(in oklch, ${colorVar} 14%, transparent)`,
            }
          : undefined
      }
    >
      {label}
    </span>
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
    <Card className="space-y-4 p-4">
      {brief.crux && (
        <p className="flex items-start gap-1.5 text-sm text-foreground">
          <Target size={14} className="mt-0.5 shrink-0 text-muted-foreground" />
          <span>
            <span className="font-medium">共同焦点：</span>
            {brief.crux}
          </span>
        </p>
      )}
      <YourCallZone divided={!!brief.crux} handoffs={handoffs} />
    </Card>
  );
}

/**
 * 留给你的：与裁决同一套 SectionLabel + 列表，不另起蓝底壳、不挂关口按钮。
 * value = 问句（对照小条不是选项）；fact = 还没核实；question = 只能等。
 */
function YourCallZone({
  handoffs,
  recommendation,
  form,
  divided = false,
  shell = "plain",
}: {
  handoffs: DebateHandoffInfo[];
  recommendation?: string;
  form?: "debate" | "red_team";
  divided?: boolean;
  shell?: "plain" | "card";
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

  const inner = (
    <div
      className={cn("space-y-3", divided && "mt-3 border-t border-border pt-3")}
    >
      <SectionLabel className="flex items-center gap-1">
        <UserRound size={13} />
        留给你的
      </SectionLabel>
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
        <ul className="space-y-3">
          {values.map((it) => (
            <li key={it.text}>
              <ValueCallItem text={it.text} />
            </li>
          ))}
        </ul>
      )}
      {facts.length > 0 && (
        <div className="space-y-2">
          <SectionLabel>还没核实</SectionLabel>
          <ul className="space-y-2">
            {facts.map((it) => (
              <li key={it.text}>
                <FactItem text={it.text} />
              </li>
            ))}
          </ul>
        </div>
      )}
      {questions.length > 0 && (
        <div className="space-y-1.5">
          <SectionLabel>只能等</SectionLabel>
          <p className="text-sm text-muted-foreground">
            {questions.map((h) => h.text).join("；")}
          </p>
        </div>
      )}
    </div>
  );

  return shell === "card" ? <Card className="p-4">{inner}</Card> : inner;
}

/** value：问句 + 可选对照条（不是可点选项）。 */
function ValueCallItem({ text }: { text: string }) {
  const { question, mappings } = splitValueCall(text);
  const questionMark = /[？?。！!…]$/.test(question) ? "" : "？";
  return (
    <div className="space-y-1.5">
      <p className="text-sm font-medium leading-snug text-foreground">
        {question}
        {questionMark ? (
          <span className="text-muted-foreground">{questionMark}</span>
        ) : null}
      </p>
      {mappings.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {mappings.map((m) => (
            <span
              key={m}
              className="rounded-lg bg-muted px-1.5 py-0.5 text-xs text-muted-foreground"
            >
              {m}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function FactItem({ text }: { text: string }) {
  const { body, statusLabels } = splitFactDisplay(text);
  return (
    <p className="flex flex-wrap items-baseline gap-1.5 text-sm text-foreground">
      {statusLabels.map((label) => (
        <span key={label} className={statusPillInline.muted}>
          {label}
        </span>
      ))}
      <span>{body || text}</span>
    </p>
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
