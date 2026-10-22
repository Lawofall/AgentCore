/** 相机图长边；相册超阈值或 HEIC 同样压到此边。 */
export const CAMERA_IMAGE_MAX_EDGE = 2048;
export const CAMERA_JPEG_QUALITY = 0.85;
/** 相册原件小于此且已是位图则不压（截图/小图保真）。 */
export const GALLERY_COMPRESS_MIN_BYTES = 2 * 1024 * 1024;

export type AttachSource = "camera" | "gallery" | "files";

export function isHeicImage(file: File): boolean {
  const type = file.type.toLowerCase();
  if (type === "image/heic" || type === "image/heif") return true;
  return /\.hei[cf]$/i.test(file.name);
}

/** 动图走原件；文件选择器里的图也不自动压。 */
export function shouldCompressImage(file: File, source: AttachSource): boolean {
  if (source === "files") return false;
  if (!file.type.startsWith("image/") && !isHeicImage(file)) return false;
  if (file.type === "image/gif") return false;
  if (source === "camera") return true;
  return isHeicImage(file) || file.size >= GALLERY_COMPRESS_MIN_BYTES;
}

function jpegName(name: string): string {
  const base = name.replace(/\.[^.]+$/, "") || "image";
  return `${base}.jpg`;
}

function scaleToMaxEdge(
  width: number,
  height: number,
  maxEdge: number,
): { w: number; h: number } {
  const long = Math.max(width, height);
  if (long <= maxEdge) return { w: width, h: height };
  const scale = maxEdge / long;
  return {
    w: Math.max(1, Math.round(width * scale)),
    h: Math.max(1, Math.round(height * scale)),
  };
}

type DecodedImage = {
  width: number;
  height: number;
  drawTo(ctx: CanvasRenderingContext2D, w: number, h: number): void;
  close(): void;
};

async function decodeImage(file: File): Promise<DecodedImage> {
  if (typeof createImageBitmap === "function") {
    const bitmap = await createImageBitmap(file);
    return {
      width: bitmap.width,
      height: bitmap.height,
      drawTo(ctx, w, h) {
        ctx.drawImage(bitmap, 0, 0, w, h);
      },
      close() {
        bitmap.close();
      },
    };
  }
  const url = URL.createObjectURL(file);
  try {
    const img = await new Promise<HTMLImageElement>((resolve, reject) => {
      const el = new Image();
      el.onload = () => resolve(el);
      el.onerror = () => reject(new Error("image decode failed"));
      el.src = url;
    });
    return {
      width: img.naturalWidth || img.width,
      height: img.naturalHeight || img.height,
      drawTo(ctx, w, h) {
        ctx.drawImage(img, 0, 0, w, h);
      },
      close() {},
    };
  } finally {
    URL.revokeObjectURL(url);
  }
}

function canvasToJpeg(
  canvas: HTMLCanvasElement,
  quality: number,
): Promise<Blob | null> {
  return new Promise((resolve) => {
    canvas.toBlob((blob) => resolve(blob), "image/jpeg", quality);
  });
}

/**
 * 解码失败或压完更大则还原件。相机/大图相册走 JPEG，方便识图与上传。
 */
export async function compressImageFile(
  file: File,
  opts: { maxEdge?: number; quality?: number } = {},
): Promise<File> {
  const maxEdge = opts.maxEdge ?? CAMERA_IMAGE_MAX_EDGE;
  const quality = opts.quality ?? CAMERA_JPEG_QUALITY;
  try {
    const decoded = await decodeImage(file);
    try {
      const { w, h } = scaleToMaxEdge(decoded.width, decoded.height, maxEdge);
      const canvas = document.createElement("canvas");
      canvas.width = w;
      canvas.height = h;
      const ctx = canvas.getContext("2d");
      if (!ctx) return file;
      decoded.drawTo(ctx, w, h);
      const blob = await canvasToJpeg(canvas, quality);
      if (!blob || blob.size === 0) return file;
      if (blob.size >= file.size && !isHeicImage(file)) return file;
      return new File([blob], jpegName(file.name), { type: "image/jpeg" });
    } finally {
      decoded.close();
    }
  } catch {
    return file;
  }
}

export async function prepareAttachFiles(
  files: readonly File[],
  source: AttachSource,
): Promise<File[]> {
  return Promise.all(
    files.map((file) =>
      shouldCompressImage(file, source)
        ? compressImageFile(file)
        : Promise.resolve(file),
    ),
  );
}
