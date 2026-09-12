import { Button, IconButton } from "@/components/ui";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { SimpleTooltip } from "@/components/ui/tooltip";
import { getConversations } from "@/hooks/useConversations";
import { copyText } from "@/lib/clipboard";
import { formatMessageTime } from "@/lib/format";
import { notifyError, notifySuccess } from "@/lib/toast";
import {
  type CreateShareOptions,
  type Share,
  createShare,
  listShares,
  revokeShare,
  shareLink,
} from "@/services/sharing";
import { useShareStore } from "@/stores/share";
import { Check, Copy, Link2, Loader2, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

type ShareExpiryChoice =
  | NonNullable<CreateShareOptions["expires_in_days"]>
  | "never";

const EXPIRY_OPTIONS: { value: ShareExpiryChoice; label: string }[] = [
  { value: 7, label: "7 天" },
  { value: 30, label: "30 天" },
  { value: "never", label: "永久" },
];

/**
 * The「分享对话」dialog (mounted once at the app shell, driven by {@link useShareStore}).
 *
 * Lists a conversation's active public links and lets the owner mint a new one or
 * revoke existing ones. Each link is a frozen content-only snapshot (问答正文) taken
 * at creation — later edits / new turns never change it (所见即所享) — so the copy
 * spells that out. The body is keyed by conversation id so switching targets always
 * starts from a fresh fetch.
 */
export function ShareConversationDialog() {
  const conversationId = useShareStore((s) => s.conversationId);
  const close = useShareStore((s) => s.close);

  return (
    <Dialog
      open={conversationId !== null}
      onOpenChange={(o) => {
        if (!o) close();
      }}
    >
      {conversationId !== null && (
        <ShareDialogBody key={conversationId} conversationId={conversationId} />
      )}
    </Dialog>
  );
}

function ShareDialogBody({ conversationId }: { conversationId: string }) {
  const [shares, setShares] = useState<Share[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [expiry, setExpiry] = useState<ShareExpiryChoice>(30);
  const title = getConversations().find((c) => c.id === conversationId)?.title;

  const reload = useCallback(async () => {
    try {
      setShares(await listShares(conversationId));
    } catch (e) {
      notifyError(e, "加载分享链接失败");
      setShares([]);
    }
  }, [conversationId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const handleCreate = async () => {
    setCreating(true);
    try {
      const options: CreateShareOptions = {
        expires_in_days: expiry === "never" ? null : expiry,
      };
      const share = await createShare(conversationId, options);
      setShares((prev) => [share, ...(prev ?? [])]);
      await copyText(shareLink(share));
    } catch (e) {
      notifyError(e, "创建分享链接失败");
    } finally {
      setCreating(false);
    }
  };

  const handleCopy = async (share: Share) => {
    if (await copyText(shareLink(share))) notifySuccess("链接已复制");
    else notifyError("复制失败");
  };

  const handleRevoke = async (share: Share) => {
    // Drop optimistically, restore on failure — a revoke is the privacy-sensitive
    // action here, so reflect it instantly and only put the row back if it failed.
    setShares((prev) => prev?.filter((s) => s.id !== share.id) ?? null);
    try {
      await revokeShare(conversationId, share.id);
    } catch (e) {
      notifyError(e, "撤销失败");
      setShares((prev) => [share, ...(prev ?? [])]);
    }
  };

  return (
    <DialogContent size="md">
      <DialogHeader>
        <DialogTitle>分享对话</DialogTitle>
        <DialogDescription>
          {title ? `「${title}」` : "该对话"}
          的只读公开链接。链接是分享时的问答快照，之后的新消息不会出现；可随时撤销。
        </DialogDescription>
      </DialogHeader>

      <DialogBody className="pb-2">
        <p className="mb-2 text-xs text-muted-foreground">链接有效期</p>
        <div className="flex flex-wrap gap-2">
          {EXPIRY_OPTIONS.map((opt) => (
            <Button
              key={String(opt.value)}
              type="button"
              variant={expiry === opt.value ? "primary" : "neutral"}
              className="h-8 px-3 text-xs"
              disabled={creating}
              onClick={() => setExpiry(opt.value)}
            >
              {opt.label}
            </Button>
          ))}
        </div>
      </DialogBody>

      <DialogBody className="max-h-[40vh]">
        {shares === null ? (
          <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            加载中…
          </div>
        ) : shares.length === 0 ? (
          <p className="py-6 text-sm text-muted-foreground">
            还没有分享链接。点击下方「新建分享链接」生成一个。
          </p>
        ) : (
          <ul className="flex flex-col gap-2 py-1">
            {shares.map((share) => (
              <li
                key={share.id}
                className="flex items-center gap-2 rounded-lg border border-border px-3 py-2"
              >
                <Link2 size={14} className="shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm text-foreground">
                    {shareLink(share)}
                  </div>
                  {share.title && share.title !== title ? (
                    <div
                      className="truncate text-xs text-muted-foreground"
                      title={share.title}
                    >
                      快照标题：{share.title}
                    </div>
                  ) : null}
                  <div className="text-xs text-muted-foreground">
                    {formatMessageTime(share.created_at)} 创建
                    {share.expires_at
                      ? ` · ${formatMessageTime(share.expires_at)} 过期`
                      : " · 永不过期"}
                  </div>
                </div>
                <SimpleTooltip label="复制链接">
                  <IconButton
                    aria-label="复制链接"
                    onClick={() => void handleCopy(share)}
                  >
                    <Copy size={14} />
                  </IconButton>
                </SimpleTooltip>
                <SimpleTooltip label="撤销链接">
                  <IconButton
                    aria-label="撤销链接"
                    onClick={() => void handleRevoke(share)}
                    className="hover:bg-destructive/10 hover:text-destructive"
                  >
                    <Trash2 size={14} />
                  </IconButton>
                </SimpleTooltip>
              </li>
            ))}
          </ul>
        )}
      </DialogBody>

      <DialogFooter>
        <Button
          className="h-9 px-4"
          disabled={creating || shares === null}
          icon={
            creating ? (
              <Loader2 size={14} className="animate-spin" />
            ) : shares && shares.length > 0 ? (
              <Plus size={14} />
            ) : (
              <Check size={14} />
            )
          }
          onClick={() => void handleCreate()}
        >
          {shares && shares.length > 0 ? "新建分享链接" : "创建分享链接"}
        </Button>
      </DialogFooter>
    </DialogContent>
  );
}
