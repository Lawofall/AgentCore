import { debateSideColorVar } from "@/components/chat/debate/model/labels";
import { agentColorVar, agentGlyph } from "@/lib/agentIdentity";
import { STANCE_META, type Stance } from "@/stores/execution";

export interface DebateGraphIdentity {
  color: string;
  glyph: string;
  /** 默认「正方 / 反方」不占标题；自定义 side.name 仍写。 */
  showRoleTitle: boolean;
}

function impliedStance(
  role: string,
  stance: Stance | null | undefined,
): Stance | null {
  if (stance) return stance;
  const trimmed = role.trim();
  if (trimmed === STANCE_META.pro.label) return "pro";
  if (trimmed === STANCE_META.con.label) return "con";
  return null;
}

/** 角色名是否为默认立场分类词（正方 / 反方）。 */
export function isGenericDebateSideName(role: string): boolean {
  const trimmed = role.trim();
  return trimmed === STANCE_META.pro.label || trimmed === STANCE_META.con.label;
}

/**
 * 协作图辩手身份：正反走阵营色（`--debate-side-pro/con`），默认名不占标题、
 * 头像字用「正 / 反」。无立场的节点（主持人等）仍按角色名 hash。
 */
export function debateGraphIdentity(input: {
  role: string;
  stance?: Stance | null;
}): DebateGraphIdentity {
  const { role } = input;
  const implied = impliedStance(role, input.stance);
  const generic = isGenericDebateSideName(role);
  if (implied) {
    return {
      color: debateSideColorVar(implied, role),
      glyph: generic ? STANCE_META[implied].short : agentGlyph(role),
      showRoleTitle: !generic && Boolean(role.trim()),
    };
  }
  return {
    color: agentColorVar(role),
    glyph: agentGlyph(role),
    showRoleTitle: Boolean(role.trim()),
  };
}
