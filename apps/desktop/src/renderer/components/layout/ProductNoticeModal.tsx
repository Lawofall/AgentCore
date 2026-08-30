import { Button } from "@/components/ui";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useProductNoticesStore } from "@/stores/productNotices";
import { useNavigate } from "react-router-dom";
import { openNoticeCta } from "./ProductNoticeBanner";

/**
 * Product notice modal (surface=modal). Once closed → server dismiss; never snoozed.
 * Body scrolls so long copy does not blow past the viewport.
 */
export function ProductNoticeModal() {
  const navigate = useNavigate();
  const modal = useProductNoticesStore((s) => s.modal);
  const dismiss = useProductNoticesStore((s) => s.dismiss);

  const open = modal != null;

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && modal) void dismiss(modal.id);
      }}
    >
      {modal ? (
        <DialogContent
          className="flex max-h-[min(80vh,32rem)] max-w-md flex-col gap-0 p-0"
          showClose
        >
          <DialogHeader className="pr-10">
            <DialogTitle>{modal.title}</DialogTitle>
          </DialogHeader>

          <DialogDescription asChild>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-2">
              <p className="whitespace-pre-wrap text-sm text-muted-foreground">
                {modal.body}
              </p>
            </div>
          </DialogDescription>

          <DialogFooter>
            <Button
              variant="neutral"
              size="md"
              onClick={() => void dismiss(modal.id)}
            >
              知道了
            </Button>
            {modal.cta_label && modal.cta_url ? (
              <Button
                variant="primary"
                size="md"
                onClick={() => {
                  const url = modal.cta_url;
                  if (url) openNoticeCta(url, navigate);
                }}
              >
                {modal.cta_label}
              </Button>
            ) : null}
          </DialogFooter>
        </DialogContent>
      ) : null}
    </Dialog>
  );
}
