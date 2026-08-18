// Model catalog (模型目录) REST + shared cache for the mobile client.
//
// `GET /v1/users/me/models` lists models this user may pick for profile slots
// (greyed when they need a BYOK key they lack). Chat no longer picks bare models —
// see api/modelProfiles.ts. Settings slot selectors still use this catalog.
import { apiFetch } from "@/api/client";
import type { components } from "@/types/api.generated";
import { useEffect, useState } from "react";

type Schemas = components["schemas"];
type RawCatalog = Schemas["ModelCatalogResponse"];

/** The account's currently-resolved model (+ which BYOK provider it runs on). */
export type ModelCatalogCurrent = RawCatalog["current"] & {
  provider_id?: string | null;
};
/** One selectable (or greyed-out) model row, keyed by (id, origin, provider_id). */
export type ModelCatalogItem = RawCatalog["models"][number] & {
  provider_id?: string | null;
  provider_label?: string | null;
};
/** The user's selectable model catalog + the account's currently-resolved model. */
export type ModelCatalog = Omit<RawCatalog, "current" | "models"> & {
  current: ModelCatalogCurrent;
  models: ModelCatalogItem[];
};
/** Credential origin when a model is selected. */
export type ModelOrigin = ModelCatalogItem["origin"];
/** Structured reason a listed row cannot be selected (clients render copy). */
export type ModelUnavailableReason = NonNullable<
  ModelCatalogItem["unavailable_reason"]
>;

/** Client-side copy for a structured catalog unavailability. Null if unknown/absent. */
export function unavailableReasonCopy(
  reason: ModelCatalogItem["unavailable_reason"] | undefined | null,
): string | null {
  if (!reason) return null;
  if (reason.code === "upstream_protocol_unsupported") {
    if (reason.required_protocol === "openai_responses") {
      return "需要 OpenAI /responses 协议，当前接入不支持";
    }
    if (reason.required_protocol === "anthropic_messages") {
      return "需要 Anthropic /messages 协议，当前接入不支持";
    }
    return "当前接入不支持该模型所需协议";
  }
  return null;
}

/** Slot selection key. For BYOK, `providerId` disambiguates the same model id across providers. */
export type ModelPick = {
  id: string;
  origin: ModelOrigin;
  providerId?: string | null;
};

/** Fetch the user's model catalog (owner-scoped "me"). */
export async function getModels(): Promise<ModelCatalog> {
  const res = await apiFetch("/v1/users/me/models");
  if (!res.ok) throw new Error(`加载模型目录失败 (${res.status})`);
  return (await res.json()) as ModelCatalog;
}

export function modelPickKey(pick: ModelPick): string {
  return `${pick.id}:${pick.origin}:${pick.providerId ?? ""}`;
}

export function picksEqual(
  a: ModelPick | null | undefined,
  b: ModelPick | null | undefined,
): boolean {
  if (!a || !b) return a === b;
  return (
    a.id === b.id &&
    a.origin === b.origin &&
    (a.providerId ?? null) === (b.providerId ?? null)
  );
}

/** Build a pick for a BYOK model — only tags `providerId` when one is known. */
export function byokPick(
  id: string,
  providerId: string | null | undefined,
): ModelPick {
  const pid = providerId?.trim();
  return pid ? { id, origin: "byok", providerId: pid } : { id, origin: "byok" };
}

export function findCatalogItem(
  catalog: ModelCatalog | null,
  pick: ModelPick,
): ModelCatalogItem | undefined {
  const models = catalog?.models;
  if (!models) return undefined;
  return (
    models.find(
      (m) =>
        m.id === pick.id &&
        m.origin === pick.origin &&
        (m.provider_id ?? null) === (pick.providerId ?? null),
    ) ??
    models.find((m) => m.id === pick.id && m.origin === pick.origin) ??
    models.find((m) => m.id === pick.id)
  );
}

export function modelDisplayLabel(
  catalog: ModelCatalog | null,
  pick: ModelPick | null | undefined,
): string | null {
  if (!pick) return null;
  return findCatalogItem(catalog, pick)?.display_name ?? pick.id;
}

// --- shared cache (no react-query) ------------------------------------------------------
let cache: ModelCatalog | null = null;
let inflight: Promise<ModelCatalog> | null = null;
const subscribers = new Set<(c: ModelCatalog) => void>();

async function load(force: boolean): Promise<void> {
  if (!force && cache) return;
  if (!inflight) inflight = getModels();
  try {
    const next = await inflight;
    cache = next;
    for (const fn of subscribers) fn(next);
  } finally {
    inflight = null;
  }
}

export interface UseModelsResult {
  data: ModelCatalog | null;
  loading: boolean;
  error: string | null;
  refetch: () => void;
}

/**
 * Subscribe to the shared model catalog. Settings slot editors pass `{ force: true }`
 * so opening revalidates availability after a key was just added.
 */
export function useModels(opts?: { force?: boolean }): UseModelsResult {
  const force = opts?.force ?? false;
  const [data, setData] = useState<ModelCatalog | null>(cache);
  const [loading, setLoading] = useState(!cache);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const sub = (c: ModelCatalog) => {
      if (alive) setData(c);
    };
    subscribers.add(sub);
    if (cache) setData(cache);
    if (!force && cache) {
      setLoading(false);
    } else {
      setLoading(true);
      setError(null);
      load(force)
        .catch((e) => {
          if (alive) {
            setError(e instanceof Error ? e.message : "加载模型目录失败");
          }
        })
        .finally(() => {
          if (alive) setLoading(false);
        });
    }
    return () => {
      alive = false;
      subscribers.delete(sub);
    };
  }, [force]);

  const refetch = () => {
    void load(true).catch(() => {
      /* surfaced on the next mounted render via the shared error path */
    });
  };

  return { data, loading, error, refetch };
}
