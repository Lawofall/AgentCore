import {
  noticeSeverityTone,
  openNoticeCta,
} from "@/components/layout/ProductNoticeBanner";
import { Button, IconButton } from "@/components/ui";
import { statusChip } from "@/components/ui/tone-presets";
import { cn } from "@/lib/utils";
import type { ChatMessageDetail } from "@/services/messaging";
import type { ActiveNotice } from "@/services/notices";
import { useMessagingStore } from "@/stores/messaging";
import { useProductNoticesStore } from "@/stores/productNotices";
import { ArrowLeft } from "lucide-react";
import { useEffect, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import {
  SEVERITY_LABEL,
  asProductNoticePayload,
  findProductNoticeMessage,
  optionalString,
  splitNoticeContent,
} from "./productNotice";

export interface ProductNoticeDetailView {
  title: string;
  body: string;
  severity: string;
  coverUrl: string | null;
  ctaLabel: string | null;
  ctaUrl: string | null;
  createdAt: string | null;
}

/** Build detail view from an already-loaded IM system_card. */
export function detailFromMessage(
  message: ChatMessageDetail,
): ProductNoticeDetailView | null {
  const payload = asProductNoticePayload(message.payload);
  if (!payload) return null;
  const { title, body } = splitNoticeContent(message.content);
  return {
    title,
    body,
    severity:
      typeof payload.severity === "string" && payload.severity
        ? payload.severity
        : "normal",
    coverUrl: optionalString(payload.cover_url),
    ctaLabel: optionalString(payload.cta_label),
    ctaUrl: optionalString(payload.cta_url),
    createdAt: message.created_at,
  };
}

/** Fallback when the IM row is not in the loaded window but active inbox still has it. */
export function detailFromActiveNotice(
  notice: ActiveNotice,
): ProductNoticeDetailView {
  return {
    title: notice.title,
    body: notice.body,
    severity: notice.severity || "normal",
    coverUrl: optionalString(notice.cover_url),
    ctaLabel: optionalString(notice.cta_label),
    ctaUrl: optionalString(notice.cta_url),
    createdAt: notice.published_at,
  };
}

/**
 * In-app product notice detail (消息域深链).
 * Prefers the loaded chat message; falls back to active inbox when present.
 */
export function ProductNoticeDetail({
  chatId,
  noticeId,
}: {
  chatId: string;
  noticeId: string;
}) {
  const navigate = useNavigate();
  const messages = useMessagingStore((s) => s.messagesByChat[chatId] ?? null);
  const inbox = useProductNoticesStore((s) => s.inbox);
  const openChat = useMessagingStore((s) => s.openChat);
  const activeChatId = useMessagingStore((s) => s.activeChatId);

  useEffect(() => {
    if (chatId !== activeChatId) void openChat(chatId);
  }, [chatId, activeChatId, openChat]);

  const detail = useMemo(() => {
    if (messages) {
      const hit = findProductNoticeMessage(messages, noticeId);
      if (hit) return detailFromMessage(hit);
    }
    const active = inbox.find((n) => n.id === noticeId);
    if (active) return detailFromActiveNotice(active);
    return null;
  }, [messages, noticeId, inbox]);

  const loading = messages === null && !detail;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex shrink-0 items-center gap-2 border-b border-border px-3 py-2">
        <IconButton
          size="sm"
          aria-label="返回会话"
          onClick={() => navigate(`/messages/${encodeURIComponent(chatId)}`)}
        >
          <ArrowLeft size={16} />
        </IconButton>
        <h2 className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
          公告详情
        </h2>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        {loading ? (
          <p className="p-6 text-center text-sm text-muted-foreground">
            加载中…
          </p>
        ) : !detail ? (
          <p className="p-6 text-center text-sm text-muted-foreground">
            找不到这条公告（可能尚未加载到本地或已过期）
          </p>
        ) : (
          <NoticeDetailBody detail={detail} />
        )}
      </div>
    </div>
  );
}

function NoticeDetailBody({ detail }: { detail: ProductNoticeDetailView }) {
  const navigate = useNavigate();
  const tone = noticeSeverityTone(detail.severity);
  const timeLabel = detail.createdAt
    ? new Date(detail.createdAt).toLocaleString()
    : null;

  return (
    <article className="mx-auto w-full max-w-2xl px-4 py-4">
      {detail.coverUrl ? (
        <img
          src={detail.coverUrl}
          alt=""
          className="mb-4 aspect-[2/1] w-full rounded-xl object-cover"
        />
      ) : (
        <div
          className={cn(
            "mb-4 flex items-center justify-between gap-2 rounded-xl border px-3 py-2 text-xs",
            statusChip[tone],
          )}
        >
          <span className="font-medium">
            {SEVERITY_LABEL[detail.severity] ?? detail.severity}
          </span>
          {timeLabel ? (
            <span className="tabular-nums opacity-90">{timeLabel}</span>
          ) : null}
        </div>
      )}

      <h1 className="text-xl font-semibold text-foreground">{detail.title}</h1>
      {detail.coverUrl && timeLabel ? (
        <p className="mt-1 text-xs text-muted-foreground">{timeLabel}</p>
      ) : null}

      {detail.body ? (
        <p className="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-foreground">
          {detail.body}
        </p>
      ) : null}

      {detail.ctaLabel && detail.ctaUrl ? (
        <div className="mt-6">
          <Button
            variant="primary"
            size="sm"
            onClick={() => openNoticeCta(detail.ctaUrl as string, navigate)}
          >
            {detail.ctaLabel}
          </Button>
        </div>
      ) : null}
    </article>
  );
}
