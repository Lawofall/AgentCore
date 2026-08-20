/**
 * ProjectedTurn.interactions[] fold (提问确认统一重构 P3).
 * Mirrors apps/server/.../pending_interactions.fold_interactions + project_interaction_leaf.
 * Desktop-local copy — do not import from mobile (cross-platform-frontend.mdc).
 */
import type {
  InteractionStatus,
  ProjectedInteraction,
} from "@agentcore/protocol-conformance/projectedTurn";
import { GATE_INTERACTION_KINDS } from "@agentcore/protocol-conformance/projectedTurn";

type Wire = Record<string, unknown>;

interface Open {
  leaf: ProjectedInteraction;
  order: number;
}

function keyOf(kind: string, id: string): string {
  return `${kind}:${id}`;
}

function asRecord(v: unknown): Wire {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Wire) : {};
}

function str(v: unknown, fallback = ""): string {
  return typeof v === "string" ? v : fallback;
}

function upsert(
  map: Map<string, Open>,
  order: { n: number },
  leaf: ProjectedInteraction,
): void {
  const k = keyOf(leaf.kind, leaf.id);
  const prev = map.get(k);
  if (
    prev &&
    (prev.leaf.status === "resolved" || prev.leaf.status === "orphaned")
  ) {
    return;
  }
  if (!prev) {
    map.set(k, { leaf, order: order.n++ });
  } else {
    prev.leaf = leaf;
  }
}

function settle(
  map: Map<string, Open>,
  kind: ProjectedInteraction["kind"],
  id: string,
  status: Extract<InteractionStatus, "resolved" | "orphaned">,
): void {
  const prev = map.get(keyOf(kind, id));
  if (!prev || prev.leaf.status !== "pending") return;
  prev.leaf = { ...prev.leaf, status };
}

/** Fold SSE events → interactions[] (insertion order of required). */
export function foldInteractions(
  events: Array<{ type: string; payload: unknown }>,
): ProjectedInteraction[] {
  const map = new Map<string, Open>();
  const order = { n: 0 };

  for (const ev of events) {
    const p = asRecord(ev.payload);
    switch (ev.type) {
      case "approval_required": {
        const id = str(p.approval_id);
        if (!id) break;
        upsert(map, order, {
          kind: "approval",
          id,
          status: "pending",
          toolCallId: str(p.tool_call_id),
          toolName: str(p.tool_name),
          arguments: asRecord(p.arguments),
        });
        break;
      }
      case "approval_resolved": {
        const id = str(p.approval_id);
        if (id) settle(map, "approval", id, "resolved");
        break;
      }
      case "escalation_required": {
        if (p.awaiting === "ceo") break;
        const id = str(p.escalation_id);
        if (!id) break;
        const leaf: ProjectedInteraction = {
          kind: "escalation",
          id,
          status: "pending",
          runId: str(p.run_id),
          agentId: str(p.agent_id),
          question: str(p.question),
          assumption: str(p.assumption),
        };
        if (p.awaiting === "user" || p.awaiting === "ceo") {
          leaf.awaiting = p.awaiting;
        }
        upsert(map, order, leaf);
        break;
      }
      case "escalation_resolved": {
        const id = str(p.escalation_id);
        if (id) settle(map, "escalation", id, "resolved");
        break;
      }
      case "checkpoint_required": {
        const id = str(p.checkpoint_id);
        if (!id) break;
        upsert(map, order, {
          kind: "ask_user",
          id,
          status: "pending",
          question: str(p.question),
          context: str(p.context),
        });
        break;
      }
      case "checkpoint_resolved": {
        const id = str(p.checkpoint_id);
        if (id) settle(map, "ask_user", id, "resolved");
        break;
      }
      case "plan_review_required": {
        const id = str(p.checkpoint_id);
        if (!id) break;
        const steps = Array.isArray(p.steps) ? p.steps : [];
        const runIds = steps.map((s) => str(asRecord(s).run_id));
        upsert(map, order, {
          kind: "plan_review",
          id,
          status: "pending",
          runIds,
        });
        break;
      }
      case "plan_review_resolved": {
        const id = str(p.checkpoint_id);
        if (id) settle(map, "plan_review", id, "resolved");
        break;
      }
      case "team_preview_required": {
        const id = str(p.checkpoint_id);
        if (!id) break;
        const workers = Array.isArray(p.workers) ? p.workers : [];
        const workerIds = workers.map((w) => str(asRecord(w).run_id));
        upsert(map, order, {
          kind: "team_preview",
          id,
          status: "pending",
          workerIds,
        });
        break;
      }
      case "team_preview_resolved": {
        const id = str(p.checkpoint_id);
        if (!id) break;
        const prev = map.get(keyOf("team_preview", id));
        if (
          !prev ||
          prev.leaf.status !== "pending" ||
          prev.leaf.kind !== "team_preview"
        ) {
          break;
        }
        const excluded = Array.isArray(p.excluded_run_ids)
          ? p.excluded_run_ids.filter(
              (x): x is string => typeof x === "string" && x.length > 0,
            )
          : [];
        const overridesRaw = Array.isArray(p.write_capability_overrides)
          ? p.write_capability_overrides
          : [];
        const overrides: Array<{ runId: string; capability: "text_only" }> = [];
        for (const row of overridesRaw) {
          const r = asRecord(row);
          const rid = str(r.run_id);
          if (!rid) continue;
          overrides.push({ runId: rid, capability: "text_only" });
        }
        const modelOverridesRaw = asRecord(p.model_overrides);
        const modelOverrides: Record<
          string,
          { model: string; origin?: string; provider_id?: string }
        > = {};
        for (const [rid, row] of Object.entries(modelOverridesRaw)) {
          if (!rid || typeof row !== "object" || row === null) continue;
          const r = asRecord(row);
          const model = str(r.model);
          if (!model) continue;
          const entry: {
            model: string;
            origin?: string;
            provider_id?: string;
          } = { model };
          const origin = str(r.origin);
          if (origin) entry.origin = origin;
          const providerId = str(r.provider_id);
          if (providerId) entry.provider_id = providerId;
          modelOverrides[rid] = entry;
        }
        prev.leaf = {
          ...prev.leaf,
          status: "resolved",
          ...(excluded.length ? { excludedRunIds: excluded } : {}),
          ...(overrides.length ? { writeCapabilityOverrides: overrides } : {}),
          ...(Object.keys(modelOverrides).length ? { modelOverrides } : {}),
        };
        break;
      }
      case "stage_card_required": {
        const id = str(p.stage_card_id);
        if (!id) break;
        const sides = Array.isArray(p.sides)
          ? p.sides.map((s) => {
              const r = asRecord(s);
              return {
                key: str(r.key),
                name: str(r.name),
                stance: str(r.stance),
              };
            })
          : [];
        const ptrs = Array.isArray(p.fact_pointers)
          ? p.fact_pointers.map((x) => str(x))
          : [];
        upsert(map, order, {
          kind: "stage_card",
          id,
          status: "pending",
          motion: str(p.motion),
          sides,
          form: str(p.form) || "debate",
          rationale: str(p.rationale),
          factPointers: ptrs,
          thorough: p.thorough !== false,
          maxRounds: Number(p.max_rounds ?? 5) || 5,
          note: typeof p.note === "string" ? p.note : null,
        });
        break;
      }
      case "stage_card_resolved": {
        const id = str(p.stage_card_id);
        if (id) settle(map, "stage_card", id, "resolved");
        break;
      }
      case "interaction_orphaned": {
        const id = str(p.interaction_id);
        const kind = str(p.kind) as ProjectedInteraction["kind"];
        if (id && kind) settle(map, kind, id, "orphaned");
        break;
      }
      default:
        break;
    }
  }

  return [...map.values()].sort((a, b) => a.order - b.order).map((o) => o.leaf);
}

export function hasGatePending(interactions: ProjectedInteraction[]): boolean {
  const gates = new Set<string>(GATE_INTERACTION_KINDS);
  return interactions.some((i) => i.status === "pending" && gates.has(i.kind));
}
