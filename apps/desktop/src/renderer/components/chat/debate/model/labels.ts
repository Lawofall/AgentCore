import type { DebateSignal } from "@/components/ui/tone-presets";
import { agentColorVar } from "@/lib/agentIdentity";
import type { DebateVerdict } from "@/types/events";
import type { DebateForm, DebateRoundModel, RoundVerdictView } from "./types";

/**
 * 正反 2 方的**固定对垒色**（`pro`/`con` 语义 key → 专用辩论阵营 token）——取代「按名字 hash
 * 取色」：名字一撞 hash 就同色（真实会话里「加重派」「审慎派」双双落 `--agent-1` → 阵营分不开）。
 * 二元对抗是独立视觉语义，不走 `--agent-N` 身份色板，而用 `--debate-side-pro`（蓝）/
 * `--debate-side-con`（红）——一眼红蓝对垒、与并排左支持/右反对一致，且色相/彩度与状态色分离
 * （见 `packages/design-tokens/tokens.css` · color-tokens.mdc）。多方（圆桌 / 红队 / subject…）无
 * 对立轴 → 落回按名字 hash ({@link agentColorVar})。live↔收场同一 key 恒同色，跨群聊 / 简报 / 协作图节点一致。
 */
const DEBATE_STANCE_COLOR: Record<string, string> = {
  pro: "var(--debate-side-pro)",
  con: "var(--debate-side-con)",
};

/** 一方的身份色：正反 2 方走固定对垒色（按语义 key），其余按名字 hash。见 {@link DEBATE_STANCE_COLOR}。 */
export function debateSideColorVar(sideKey: string, name: string): string {
  return DEBATE_STANCE_COLOR[sideKey] ?? agentColorVar(name);
}

/**
 * 一轮的「交锋信号」(verdict 派生) —— 驱动辩论室时间线轴点 / 收敛信号带的配色与语义：
 * 在飞 > 收敛 > 有交锋 > 各说各话 (色板见 `debateSignalDot`)。
 */
export function roundSignal(round: DebateRoundModel): DebateSignal {
  if (round.inFlight) return "inflight";
  if (round.verdict?.converged) return "converged";
  if (round.verdict?.real_clash) return "clash";
  return "quiet";
}

/**
 * 把一轮裁判的两个**正交**维度（`real_clash` × `converged`）与收尾原因（`stop_reason`）融成
 * **一句人话**轮状态 + 悬浮解释——根治「各说各话 + 已收敛」并列读起来自相矛盾（用户反馈）：
 *  - **未收敛** → 讲这一轮打成什么样、下一步往哪走；
 *  - **已收敛** → 直接讲【为什么到此为止 / 用户该带走什么】，尤其口味/价值之争（AI 判不了、该交
 *    用户拍板）——把后端每轮已带的 `stop_reason` 兑现成人话，而非压成笼统「已收敛」。
 *
 * `label` / `hint` 同源单一 switch（不漂移）：`label` 入 pill / 脊、`hint` 入 tooltip。
 * 形态感知：圆桌「各方并非针锋相对」是常态（不说「各说各话」、讲铺光谱）、红队是单向施压。
 * 配色仍由 {@link roundSignal} 决定（收敛绿 / 交锋蓝 / 平淡灰），此函数只产**文案**。
 */
export function describeRoundVerdict(
  verdict: DebateVerdict,
  form: DebateForm,
): RoundVerdictView {
  if (verdict.converged) {
    switch (verdict.stop_reason) {
      case "focus_clarified":
        return {
          label: "价值之争 · AI 判不了，看你拍板",
          hint: "分歧落在价值 / 偏好上、没有事实对错，AI 帮不了你做这个选择——交给你拍板。",
        };
      case "red_team_exhausted":
        return {
          label: "风险已挖尽 · 可定夺",
          hint: "风险已基本挖尽、方案方也回应过，可据此定夺。",
        };
      case "all_failed":
        return {
          label: "发言失败 · 提前终止",
          hint: "本轮各方均未产出有效发言，辩论提前终止。",
        };
      default:
        if (form === "roundtable") {
          return {
            label: "观点光谱已铺满 · 见结论",
            hint: "各视角已铺开、不再冒出本质上的新视角，可看结论的观点地图。",
          };
        }
        return verdict.real_clash
          ? {
              label: "交锋充分 · 可出结论",
              hint: "双方已正面交锋、不再产生新论点，可以出结论了。",
            }
          : {
              label: "无更多新论点 · 可收尾",
              hint: "不再产生新论点，辩论可以收尾。",
            };
    }
  }
  if (form === "roundtable") {
    return {
      label: "观点还在铺开",
      hint: "各视角还在补充，观点光谱尚未铺满。",
    };
  }
  if (form === "red_team") {
    return verdict.real_clash
      ? {
          label: "红队施压中 · 方案在回应",
          hint: "红队正在挑刺施压、方案方在回应修补，风险还在挖。",
        }
      : {
          label: "风险还在挖深",
          hint: "风险尚未挖尽，红队还在深挖。",
        };
  }
  return verdict.real_clash
    ? {
        label: "正面交锋 · 还有的辩",
        hint: "双方已针锋相对回应彼此，但仍有新论点，继续辩。",
      }
    : {
        label: "各自亮立场 · 待逼出交锋",
        hint: "本轮双方各自陈述、还没真正接火，下一轮逼出交锋。",
      };
}

/** 辩论收场原因 → 中文（镜像后端 STOP_REASONS）。未知原样渲染。 */
const STOP_LABELS: Record<string, string> = {
  converged: "已收敛",
  focus_clarified: "已澄清为价值之争",
  red_team_exhausted: "风险已挖尽",
  max_rounds: "达轮次上限",
  all_failed: "发言失败提前终止",
  user_concluded: "你叫停出结论",
};

/** 辩论收场原因的人话标签（流末终审 + 右轨裁决台共用；`null` → 「已收场」）。 */
export function stopLabel(reason: string | null): string {
  if (!reason) return "已收场";
  return STOP_LABELS[reason] ?? reason;
}

/**
 * 一句「这是什么」功能说明（形态感知）——给首次用户讲清这场辩论能给他什么，贴在辩论室 / 擂台标题
 * 下。正反给决策简报、红队给风险清单、圆桌给观点地图（与 {@link describeRoundVerdict} 同口径）。
 */
export function debateFormBlurb(form: DebateForm): string {
  switch (form) {
    case "red_team":
      return "红队逐条挑刺、方案方处置、红队复核——你带走每条都有下场的 finding 台账与门决（有条件通过 / 需大改 / 不可行）。";
    case "roundtable":
      return "主持人分题点名串行对话，挖到分歧根源（crux）即止——你带走共识/分歧地图，而非强行裁定对错。";
    default:
      return "两个 AI 各执正反、多轮交锋，最后给你一份带倾向与置信度的决策简报——不是单个 AI 的一面之词。";
  }
}

/**
 * 该方是否需要单独显示模型徽章 —— 当一方的**身份名已经包含厂商名**时（如「原生DeepSeek」并排
 * 「DeepSeek」徽章 = 噪音），抑制徽章避免重复。
 */
export function shouldShowModelBadge(
  name: string,
  model: string | null | undefined,
): boolean {
  const label = modelVendorLabel(model);
  if (!label) return false;
  return !name.toLowerCase().includes(label.toLowerCase());
}

/** 厂商前缀 / 模型名 → 友好厂商名（真·多模型辩论的「谁是哪个模型」展示）。`provider/model` 前缀
 *  优先（doubao/kimi/zhipu）；无前缀按模型名识别（DeepSeek 是默认 provider、无前缀）。空 → null
 *  （不显徽章）。兜底返回前缀或原串，未知模型也给出可读名。映射随接入新厂商在此一处扩展。 */
export function modelVendorLabel(
  model: string | null | undefined,
): string | null {
  const m = (model ?? "").trim();
  if (!m) return null;
  const byPrefix: Record<string, string> = {
    doubao: "豆包",
    kimi: "Kimi",
    zhipu: "智谱",
    deepseek: "DeepSeek",
  };
  const prefix = m.includes("/") ? m.slice(0, m.indexOf("/")) : "";
  if (prefix) return byPrefix[prefix] ?? prefix;
  if (/^deepseek/i.test(m)) return "DeepSeek";
  if (/^doubao/i.test(m)) return "豆包";
  if (/^glm/i.test(m)) return "智谱";
  if (/^kimi/i.test(m)) return "Kimi";
  return m;
}
