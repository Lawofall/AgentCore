import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Spinner } from "@/components/ui/Spinner";
import { errorMessage } from "@/services/api";
import {
  type AdminUpdateUserRequest,
  type AdminUser,
  updateUser,
} from "@/services/adminUsers";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";

// Empty input = clear the override (inherit the global config). A value sets the
// override (0 = unlimited for that dimension). This dialog shows the full quota
// state, so it sends every field on save (the route's tri-state maps empty→null).
function numOrNull(s: string): number | null {
  const t = s.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

function initial(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

export function QuotaDialog({
  user,
  onClose,
  onSaved,
}: {
  user: AdminUser;
  onClose: () => void;
  onSaved: (updated: AdminUser) => void;
}) {
  const formId = useId();
  const [unlimited, setUnlimited] = useState(user.is_unlimited);
  const [dailyTokens, setDailyTokens] = useState(
    initial(user.quota_daily_tokens),
  );
  const [monthlyCost, setMonthlyCost] = useState(
    initial(user.quota_monthly_cost_cny),
  );
  const [dailyCost, setDailyCost] = useState(
    initial(user.quota_daily_cost_cny),
  );
  const [searchDaily, setSearchDaily] = useState(
    initial(user.quota_search_daily),
  );
  const [searchMonthly, setSearchMonthly] = useState(
    initial(user.quota_search_monthly),
  );
  const [saving, setSaving] = useState(false);

  const handleSave = async (e: FormEvent) => {
    e.preventDefault();
    if (saving) return;
    setSaving(true);
    const patch: AdminUpdateUserRequest = {
      is_unlimited: unlimited,
      quota_daily_tokens: numOrNull(dailyTokens),
      quota_monthly_cost_cny: numOrNull(monthlyCost),
      quota_daily_cost_cny: numOrNull(dailyCost),
      quota_search_daily: numOrNull(searchDaily),
      quota_search_monthly: numOrNull(searchMonthly),
    };
    try {
      const updated = await updateUser(user.id, patch);
      toast.success("配额已更新");
      onSaved(updated);
    } catch (err) {
      toast.error(errorMessage(err));
      setSaving(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      busy={saving}
      title="编辑配额"
      description={`${user.username} · 留空 = 继承全局，0 = 该维度不限`}
      footer={
        <>
          <Button
            variant="outline"
            size="sm"
            onClick={onClose}
            disabled={saving}
          >
            取消
          </Button>
          <Button type="submit" form={formId} size="sm" disabled={saving}>
            {saving && <Spinner />}
            保存
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={handleSave} className="flex flex-col gap-4">
        <label className="flex items-center gap-2 text-foreground text-sm">
          <input
            type="checkbox"
            checked={unlimited}
            onChange={(e) => setUnlimited(e.target.checked)}
            disabled={saving}
            className="size-4 accent-primary"
          />
          无限额（跳过全部配额检查）
        </label>

        <div className="flex flex-col gap-3">
          <Field label="日 token 上限">
            <Input
              type="number"
              min={0}
              inputMode="numeric"
              placeholder="继承全局"
              value={dailyTokens}
              onChange={(e) => setDailyTokens(e.target.value)}
              disabled={unlimited || saving}
            />
          </Field>
          <Field label="月成本上限（元）">
            <Input
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              placeholder="继承全局"
              value={monthlyCost}
              onChange={(e) => setMonthlyCost(e.target.value)}
              disabled={unlimited || saving}
            />
          </Field>
          <Field label="日成本上限（元 / 日）">
            <Input
              type="number"
              min={0}
              step="0.01"
              inputMode="decimal"
              placeholder="继承全局"
              value={dailyCost}
              onChange={(e) => setDailyCost(e.target.value)}
              disabled={unlimited || saving}
            />
          </Field>
          <Field label="日搜索次数">
            <Input
              type="number"
              min={0}
              inputMode="numeric"
              placeholder="继承全局"
              value={searchDaily}
              onChange={(e) => setSearchDaily(e.target.value)}
              disabled={unlimited || saving}
            />
          </Field>
          <Field label="月搜索次数">
            <Input
              type="number"
              min={0}
              inputMode="numeric"
              placeholder="继承全局"
              value={searchMonthly}
              onChange={(e) => setSearchMonthly(e.target.value)}
              disabled={unlimited || saving}
            />
          </Field>
        </div>
      </form>
    </Dialog>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1.5">
      <span className="font-medium text-muted-foreground text-xs">{label}</span>
      {children}
    </label>
  );
}
