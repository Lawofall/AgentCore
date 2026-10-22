import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { type AttachSource, prepareAttachFiles } from "@/lib/compressImageFile";
import { AtSign, Camera, File, Image } from "lucide-react";
import {
  type ChangeEvent,
  type ReactNode,
  forwardRef,
  useCallback,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { useComposerPlusClose } from "./ComposerPlusMenu";

export type AttachSourceHandle = {
  pickCamera: () => void;
  pickGallery: () => void;
  pickFiles: () => void;
};

function readInputFiles(event: ChangeEvent<HTMLInputElement>): File[] {
  const files = Array.from(event.target.files ?? []);
  event.target.value = "";
  return files;
}

/**
 * 原生壳三路隐藏 input：拍照（后置相机、单张）/ 相册（多图）/ 文件（任意、多选）。
 * 点选后按来源决定是否压缩，再交给 Composer / IM。
 */
export const AttachSourceInputs = forwardRef<
  AttachSourceHandle,
  { onPicked: (files: File[], source: AttachSource) => void }
>(function AttachSourceInputs({ onPicked }, ref) {
  const cameraRef = useRef<HTMLInputElement>(null);
  const galleryRef = useRef<HTMLInputElement>(null);
  const filesRef = useRef<HTMLInputElement>(null);

  const emit = useCallback(
    async (files: File[], source: AttachSource) => {
      if (files.length === 0) return;
      const prepared = await prepareAttachFiles(files, source);
      onPicked(prepared, source);
    },
    [onPicked],
  );

  useImperativeHandle(
    ref,
    () => ({
      pickCamera: () => cameraRef.current?.click(),
      pickGallery: () => galleryRef.current?.click(),
      pickFiles: () => filesRef.current?.click(),
    }),
    [],
  );

  return (
    <>
      <input
        ref={cameraRef}
        type="file"
        accept="image/*"
        capture="environment"
        className="hidden"
        tabIndex={-1}
        aria-hidden
        data-testid="attach-source-input-camera"
        onChange={(event) => void emit(readInputFiles(event), "camera")}
      />
      <input
        ref={galleryRef}
        type="file"
        accept="image/*"
        multiple
        className="hidden"
        tabIndex={-1}
        aria-hidden
        data-testid="attach-source-input-gallery"
        onChange={(event) => void emit(readInputFiles(event), "gallery")}
      />
      <input
        ref={filesRef}
        type="file"
        multiple
        className="hidden"
        tabIndex={-1}
        aria-hidden
        data-testid="attach-source-input-files"
        onChange={(event) => void emit(readInputFiles(event), "files")}
      />
    </>
  );
});

function SourceRow({
  testId,
  icon,
  label,
  onClick,
}: {
  testId: string;
  icon: ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      className="inline-flex h-8 w-full items-center gap-2 rounded-lg px-2 text-left text-sm text-foreground hover:bg-accent/60"
      onClick={onClick}
    >
      {icon}
      {label}
    </button>
  );
}

/** 「更多」或＋弹出层：拍照 / 相册 / 文件 / 可选引用。 */
export function AttachSourceRows({
  onCamera,
  onGallery,
  onFiles,
  onCite,
}: {
  onCamera: () => void;
  onGallery: () => void;
  onFiles: () => void;
  onCite?: () => void;
}) {
  const close = useComposerPlusClose();
  const go = (fn: () => void) => {
    close?.();
    fn();
  };
  return (
    <div
      className="flex w-max min-w-0 flex-col items-stretch gap-1"
      data-testid="attach-source-menu"
    >
      <SourceRow
        testId="attach-source-camera"
        icon={<Camera size={14} className="shrink-0" aria-hidden />}
        label="拍照"
        onClick={() => go(onCamera)}
      />
      <SourceRow
        testId="attach-source-gallery"
        icon={<Image size={14} className="shrink-0" aria-hidden />}
        label="相册"
        onClick={() => go(onGallery)}
      />
      <SourceRow
        testId="attach-source-files"
        icon={<File size={14} className="shrink-0" aria-hidden />}
        label="文件"
        onClick={() => go(onFiles)}
      />
      {onCite ? (
        <>
          <div className="my-0.5 h-px bg-border" />
          <SourceRow
            testId="attach-source-cite"
            icon={<AtSign size={14} className="shrink-0" aria-hidden />}
            label="引用"
            onClick={() => go(onCite)}
          />
        </>
      ) : null}
    </div>
  );
}

/** 空草稿＋ / IM 回形针：独立弹出，不叠在「更多」里。 */
export function AttachSourcePopover({
  trigger,
  onCamera,
  onGallery,
  onFiles,
  onCite,
}: {
  trigger: ReactNode;
  onCamera: () => void;
  onGallery: () => void;
  onFiles: () => void;
  onCite?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const go = (fn: () => void) => {
    setOpen(false);
    fn();
  };
  return (
    <Popover open={open} onOpenChange={setOpen} modal={false}>
      <PopoverTrigger asChild>{trigger}</PopoverTrigger>
      <PopoverContent
        align="start"
        side="top"
        avoidCollisions={false}
        className="w-max min-w-56 p-2"
        onCloseAutoFocus={(event) => event.preventDefault()}
      >
        <AttachSourceRows
          onCamera={() => go(onCamera)}
          onGallery={() => go(onGallery)}
          onFiles={() => go(onFiles)}
          onCite={onCite ? () => go(onCite) : undefined}
        />
      </PopoverContent>
    </Popover>
  );
}
