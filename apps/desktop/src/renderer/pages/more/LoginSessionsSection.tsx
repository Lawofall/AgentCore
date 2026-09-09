import {
  SettingRow,
  SettingsAsync,
  SettingsFormMessage,
  SettingsSection,
} from "@/components/settings";
import { Badge, Button, Card, ConfirmDialog } from "@/components/ui";
import { errMsg } from "@/lib/errMsg";
import {
  sessionDeviceLabel,
  sessionLastActiveLabel,
} from "@/lib/sessionDeviceLabel";
import {
  type SessionSummary,
  listSessions,
  logout,
  revokeOtherSessions,
  revokeSession,
} from "@/services/auth";
import { useAuthStore } from "@/stores/auth";
import { Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

type ConfirmTarget =
  | { kind: "one"; session: SessionSummary }
  | { kind: "others" };

/** How many rows the account page shows before "还有 N 台". Google / Microsoft
 *  security overviews use 3-5; this is the only device manager, so 5. */
const SESSION_PREVIEW_LIMIT = 5;

/**
 * 登录设备 — list active refresh-token families and revoke one / all others.
 * Placed on 账户设置 between 修改密码 and 危险区域.
 */
export function LoginSessionsSection() {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [confirm, setConfirm] = useState<ConfirmTarget | null>(null);
  const [expanded, setExpanded] = useState(false);

  const refresh = useCallback(async () => {
    setLoadError(null);
    setActionError(null);
    try {
      const res = await listSessions();
      setSessions(res.data ?? []);
    } catch (e) {
      setLoadError(errMsg(e, "加载失败，请重试"));
    }
  }, []);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    void listSessions()
      .then((res) => {
        if (!alive) return;
        setSessions(res.data ?? []);
        setLoadError(null);
      })
      .catch((e) => {
        if (alive) setLoadError(errMsg(e, "加载失败，请重试"));
      })
      .finally(() => {
        if (alive) setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const runConfirmed = async () => {
    if (!confirm) return;
    const target = confirm;
    setConfirm(null);
    setActionError(null);

    if (target.kind === "others") {
      setBusyId("others");
      try {
        await revokeOtherSessions();
        await refresh();
      } catch (e) {
        setActionError(errMsg(e, "操作失败，请重试"));
      } finally {
        setBusyId(null);
      }
      return;
    }

    const { session } = target;
    if (session.current) {
      setBusyId(session.id);
      try {
        // Current device → reuse the same logout clear-state path as UserMenu.
        try {
          await logout();
        } catch {
          /* clear the session client-side regardless of the network result */
        }
        useAuthStore.getState().setUnauthenticated();
      } finally {
        setBusyId(null);
      }
      return;
    }

    setBusyId(session.id);
    try {
      await revokeSession(session.id);
      await refresh();
    } catch (e) {
      setActionError(errMsg(e, "操作失败，请重试"));
    } finally {
      setBusyId(null);
    }
  };

  const showRevokeOthers = sessions.length > 1;
  const actionBusy = busyId !== null;
  const copy = revokeCopy(confirm);
  const hidden = Math.max(0, sessions.length - SESSION_PREVIEW_LIMIT);
  const shown = previewSessions(sessions, expanded);

  return (
    <SettingsSection
      title="登录设备"
      action={
        showRevokeOthers && !loading && !loadError ? (
          <Button
            variant="danger"
            size="md"
            disabled={actionBusy}
            onClick={() => setConfirm({ kind: "others" })}
          >
            退出其他所有设备
          </Button>
        ) : undefined
      }
    >
      <Card className="p-4">
        <SettingsAsync
          loading={loading}
          error={loadError}
          empty={sessions.length === 0}
          emptyLabel="暂无活跃登录设备"
          onRetry={() => {
            setLoading(true);
            void refresh().finally(() => setLoading(false));
          }}
        >
          <ul className="divide-y divide-border">
            {shown.map((s) => (
              <SessionRow
                key={s.id}
                session={s}
                busy={busyId === s.id}
                disabled={actionBusy}
                onRevoke={() => setConfirm({ kind: "one", session: s })}
              />
            ))}
          </ul>
          {hidden > 0 && (
            <div className="mt-1 border-t border-border pt-2">
              <Button
                variant="ghost"
                size="md"
                className="w-full"
                aria-expanded={expanded}
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? "收起" : `还有 ${hidden} 台`}
              </Button>
            </div>
          )}
        </SettingsAsync>
        <SettingsFormMessage className="mt-3">
          {actionError}
        </SettingsFormMessage>
      </Card>

      <ConfirmDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
        title={copy.title}
        description={copy.description}
        confirmLabel={copy.confirmLabel}
        tone="danger"
        onConfirm={() => void runConfirmed()}
      />
    </SettingsSection>
  );
}

/** Current device first (must not be folded away), then the rest in list order. */
function previewSessions(
  sessions: SessionSummary[],
  expanded: boolean,
): SessionSummary[] {
  if (expanded || sessions.length <= SESSION_PREVIEW_LIMIT) return sessions;
  const current = sessions.filter((s) => s.current);
  const rest = sessions.filter((s) => !s.current);
  return [...current, ...rest].slice(0, SESSION_PREVIEW_LIMIT);
}

function SessionRow({
  session,
  busy,
  disabled,
  onRevoke,
}: {
  session: SessionSummary;
  busy: boolean;
  disabled: boolean;
  onRevoke: () => void;
}) {
  const label = sessionDeviceLabel(session.platform, session.user_agent);
  const ip = session.ip?.trim() || "未知 IP";

  return (
    <li className="py-3 first:pt-0 last:pb-0">
      <SettingRow
        surface="bare"
        label={
          <span className="flex flex-wrap items-center gap-2">
            {label}
            {session.current && (
              <Badge tone="primary" pill>
                当前设备
              </Badge>
            )}
          </span>
        }
        description={
          <>
            {ip}
            <span className="mx-1.5 text-border">·</span>
            最后活跃 {sessionLastActiveLabel(session.last_used_at)}
          </>
        }
        control={
          <Button
            variant="danger"
            size="md"
            className="shrink-0"
            disabled={disabled}
            icon={
              busy ? <Loader2 size={14} className="animate-spin" /> : undefined
            }
            onClick={onRevoke}
          >
            退出
          </Button>
        }
      />
    </li>
  );
}

/** Confirm copy for the three revoke shapes; the target is null while closed. */
function revokeCopy(target: ConfirmTarget | null): {
  title: string;
  description: string;
  confirmLabel: string;
} {
  const isOthers = target?.kind === "others";
  const isCurrent = target?.kind === "one" && target.session.current;

  return {
    title: isOthers
      ? "退出其他所有设备？"
      : isCurrent
        ? "退出当前设备？"
        : "退出该设备？",
    description: isOthers
      ? "其他设备上的登录将立即失效，需要重新登录。当前设备不受影响。"
      : isCurrent
        ? "退出后将返回登录页，需要重新登录才能继续使用。"
        : "该设备上的登录将立即失效，需要重新登录。",
    confirmLabel: isOthers
      ? "退出其他设备"
      : isCurrent
        ? "退出并返回登录"
        : "确认退出",
  };
}
