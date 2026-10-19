import { SettingsSection, SettingsStack } from "@/components/settings";
import { Badge, Button, Card, Input, PageHeader } from "@/components/ui";
import { buttonClassName } from "@/components/ui/button";
import { statusChip } from "@/components/ui/tone-presets";
import {
  FEATURED_PLAN_ID,
  type PaidPlanId,
  SUBSCRIPTION_PLANS,
  SUBSCRIPTION_SHELF_ORDER,
  type SubscriptionPlan,
  type SubscriptionPlanId,
  formatPriceYuan,
  formatSeatUntil,
  planFeatureLines,
  planValuePitch,
  resolveSeatStoreUrl,
} from "@/lib/subscriptionPlans";
import { cn } from "@/lib/utils";
import { ApiError } from "@/services/api";
import { redeemSeat } from "@/services/usage";
import { useUsageStore } from "@/stores/usage";
import { Check } from "lucide-react";
import { type FormEvent, useEffect, useState } from "react";

/** 测试阶段下单口：链动小铺仍可买，先走管理员。 */
export const SUBSCRIPTION_ORDER_QQ = "837554559";

function PlanCard({
  plan,
  current,
  storeUrl,
  caption,
}: {
  plan: SubscriptionPlan;
  current: boolean;
  storeUrl: string;
  caption: string | null;
}) {
  const featured = plan.id === FEATURED_PLAN_ID;
  const features = planFeatureLines(plan);
  const pitch = planValuePitch(plan);

  return (
    <Card
      className={cn(
        "flex h-full flex-col gap-4 p-4",
        current && "bg-accent text-accent-foreground",
        featured && !current && "border-foreground",
      )}
      aria-current={current ? "true" : undefined}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-medium text-foreground">{plan.name}</p>
          <p className="mt-1 text-xs text-muted-foreground">{plan.tagline}</p>
        </div>
        {current ? (
          <Badge tone="primary" pill>
            当前
          </Badge>
        ) : featured ? (
          <Badge tone="primary" pill>
            推荐
          </Badge>
        ) : null}
      </div>

      <div>
        <p className="flex items-baseline gap-1.5 text-xl font-medium tabular-nums">
          <span>¥{formatPriceYuan(plan.priceYuan)}</span>
          <span className="text-sm font-normal text-muted-foreground">
            / 月
          </span>
        </p>
        {pitch ? (
          <p className="mt-1 text-xs text-muted-foreground">{pitch}</p>
        ) : null}
        {caption ? (
          <p className="mt-1 text-xs text-muted-foreground">{caption}</p>
        ) : null}
      </div>

      <ul className="flex flex-col gap-2">
        {features.map((line) => (
          <li key={line} className="flex items-start gap-2 text-sm">
            <Check
              size={16}
              aria-hidden
              className="mt-0.5 shrink-0 text-muted-foreground"
            />
            <span>{line}</span>
          </li>
        ))}
      </ul>

      <div className="mt-auto flex min-h-8 items-center pt-2">
        <a
          href={storeUrl}
          target="_blank"
          rel="noreferrer"
          className={buttonClassName({
            variant: featured && !current ? "primary" : "outline",
            size: "md",
            className: "w-full",
          })}
        >
          {current ? "续费" : "去购买"}
        </a>
      </div>
    </Card>
  );
}

/**
 * 订阅（/more/subscription）— 三张付费卡一列 + 链动小铺购买 + 兑换码。
 *
 * 免费档不占卡（用量页芯片仍写免费版）。购买只在链动小铺。卡密自动发货；贴码后立刻开通。席位 30 天，到期自动改回免费版。
 * 测试阶段页顶说明：先联系管理员下单。
 */
export function SubscriptionSettings({
  currentPlanId,
  storeUrl,
  seatUntil,
  pendingPlanId,
}: {
  /** Tests pass this to skip the usage fetch. Omit in product to infer. */
  currentPlanId?: SubscriptionPlanId | null;
  storeUrl?: string;
  seatUntil?: string | null;
  pendingPlanId?: PaidPlanId | null;
} = {}) {
  const summary = useUsageStore((s) => s.summary);
  const fetchSummary = useUsageStore((s) => s.fetchSummary);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);

  useEffect(() => {
    if (currentPlanId !== undefined) return;
    void fetchSummary();
  }, [currentPlanId, fetchSummary]);

  const inferred: SubscriptionPlanId =
    currentPlanId !== undefined
      ? (currentPlanId ?? "basic")
      : (summary?.seat.plan ?? "basic");
  const until =
    seatUntil !== undefined ? seatUntil : (summary?.seat.until ?? null);
  const pending =
    pendingPlanId !== undefined
      ? pendingPlanId
      : (summary?.seat.pending_plan ?? null);
  const shopUrl = resolveSeatStoreUrl(storeUrl ?? summary?.seat.store_url);

  async function onRedeem(e: FormEvent) {
    e.preventDefault();
    const trimmed = code.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setOk(null);
    try {
      const seat = await redeemSeat(trimmed);
      setCode("");
      setOk(
        seat.plan === "basic"
          ? "已兑换"
          : `已开通${SUBSCRIPTION_PLANS[seat.plan].name}`,
      );
      useUsageStore.setState((s) =>
        s.summary ? { summary: { ...s.summary, seat } } : s,
      );
      void fetchSummary();
    } catch (err) {
      const message =
        err instanceof ApiError && err.serverMessage
          ? err.serverMessage
          : "兑换失败，请重试";
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader title="订阅" />
      <SettingsStack>
        <div className="space-y-4">
          <output
            className={cn(
              "block rounded-lg border px-3 py-2 text-sm",
              statusChip.primary,
            )}
          >
            测试阶段请先联系管理员下单：QQ{" "}
            <span className="font-mono tabular-nums select-all">
              {SUBSCRIPTION_ORDER_QQ}
            </span>
          </output>
          <div className="grid items-stretch gap-4 md:grid-cols-3">
            {SUBSCRIPTION_SHELF_ORDER.map((id) => {
              const current = inferred === id;
              let caption: string | null = null;
              if (current && until) {
                const day = formatSeatUntil(until);
                const pendingName = pending
                  ? SUBSCRIPTION_PLANS[pending].name
                  : null;
                const past = Date.parse(until) < Date.now();
                if (pendingName && day) {
                  caption = `至 ${day}，其后改为${pendingName}`;
                } else if (past && day) {
                  caption = `宽限中，${day} 到期`;
                } else if (day) {
                  caption = `至 ${day}`;
                }
              }
              return (
                <PlanCard
                  key={id}
                  plan={SUBSCRIPTION_PLANS[id]}
                  current={current}
                  storeUrl={shopUrl}
                  caption={caption}
                />
              );
            })}
          </div>
        </div>
        <SettingsSection
          title="兑换码"
          description="在链动小铺购买后，订单会自动发出兑换码。贴到这里立刻开通，席位 30 天。未兑换可在小铺退款；兑换后本周期不退。"
          divider
        >
          <form
            className="flex max-w-xl flex-col gap-3 sm:flex-row sm:items-center"
            onSubmit={(e) => void onRedeem(e)}
          >
            <Input
              value={code}
              onChange={(ev) => setCode(ev.target.value)}
              placeholder="ACP-xxxx-xxxx-xxxx"
              aria-label="兑换码"
              autoComplete="off"
              spellCheck={false}
              className="font-mono"
            />
            <Button
              type="submit"
              variant="primary"
              size="md"
              disabled={busy || !code.trim()}
            >
              兑换
            </Button>
          </form>
          {error ? (
            <p className="mt-2 text-sm text-destructive" role="alert">
              {error}
            </p>
          ) : null}
          {ok ? (
            <p className="mt-2 text-sm text-muted-foreground">{ok}</p>
          ) : null}
        </SettingsSection>
      </SettingsStack>
    </div>
  );
}
