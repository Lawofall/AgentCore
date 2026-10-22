"""Decode a workspace raster into a model-bound JPEG.

Attachments and ``read`` of images share this kernel. The original file stays
on disk; only the JPEG goes to the current model as ``image_url``. Decode runs
in a killable child so a 100MP+ scan cannot OOM the API process.

Long-edge targets follow industry vision ingest (Claude standard 1568; tall /
huge scans 2048 so document text stays legible). Pillow's default pixel bomb
cap is raised only inside the worker.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agentcore.core.logging import get_logger
from agentcore.workspace.limits import VISION_TIMEOUT_SECONDS

logger = get_logger(__name__)

# Claude standard-tier long edge (also inside OpenAI high-detail / Gemini tile).
VISION_MAX_EDGE = 1568
# Document scans / tall screenshots: keep small text readable after downscale.
VISION_DOC_MAX_EDGE = 2048
VISION_DOC_LONG_EDGE_MIN = 4000
VISION_DOC_ASPECT = 2.0
VISION_JPEG_QUALITY = 75
# Allow A4-class phone scans (~139MP); reject pathological bombs.
VISION_MAX_DECODE_PIXELS = 250_000_000

_IS_WINDOWS = sys.platform == "win32"
_RASTER_META_ENV = "AGENTCORE_VISION_RASTER_META"
_RASTER_JPEG_ENV = "AGENTCORE_VISION_RASTER_JPEG"

RasterStatus = Literal["ok", "empty", "too_large", "unreadable", "timeout"]


@dataclass(frozen=True)
class RasterResult:
    """Outcome of vision preprocess. Path errors are raised by the backend, not here."""

    status: RasterStatus
    jpeg: bytes = b""
    width: int = 0
    height: int = 0
    source_width: int = 0
    source_height: int = 0
    source_bytes: int | None = None
    max_edge: int = 0
    detail: str = ""


def choose_max_edge(width: int, height: int) -> int:
    """1568 for ordinary photos; 2048 for tall or huge scans."""
    long = max(width, height)
    short = min(width, height) or 1
    if long > VISION_DOC_LONG_EDGE_MIN or (long / short) >= VISION_DOC_ASPECT:
        return VISION_DOC_MAX_EDGE
    return VISION_MAX_EDGE


def jpeg_native_part(jpeg: bytes) -> dict:
    """OpenAI ``image_url`` part for a JPEG already bound for the current model."""
    b64 = base64.b64encode(jpeg).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
    }


def fit_rgb(rgb: object, *, max_edge: int) -> object:
    """Aspect-preserving resize so neither edge exceeds ``max_edge``."""
    from PIL import Image

    if not isinstance(rgb, Image.Image):
        return rgb
    width, height = rgb.size
    longest = max(width, height)
    if longest <= max_edge:
        return rgb
    scale = max_edge / longest
    return rgb.resize(
        (
            max(1, min(max_edge, round(width * scale))),
            max(1, min(max_edge, round(height * scale))),
        ),
        Image.Resampling.LANCZOS,
    )


def rgb_jpeg_bytes(rgb: object, *, max_edge: int) -> bytes:
    """Resize an RGB PIL image (aspect preserved) and encode JPEG."""
    from PIL import Image

    fitted = fit_rgb(rgb, max_edge=max_edge)
    if not isinstance(fitted, Image.Image):
        return b""
    buf = io.BytesIO()
    fitted.save(buf, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True)
    return buf.getvalue()


def raster_image_payload(data: bytes, *, source_bytes: int | None = None) -> RasterResult:
    """In-process decode. Worker and tests call this; API parents must not."""
    if not data:
        return RasterResult(status="empty", source_bytes=0)
    from PIL import Image, UnidentifiedImageError

    _prepare_pil(Image)
    try:
        with Image.open(io.BytesIO(data)) as img:
            size = source_bytes if source_bytes is not None else len(data)
            return _raster_opened(img, source_bytes=size)
    except Image.DecompressionBombError:
        return RasterResult(
            status="unreadable",
            source_bytes=len(data),
            detail="pixels_exceeded",
        )
    except (UnidentifiedImageError, OSError, ValueError):
        return RasterResult(
            status="unreadable",
            source_bytes=len(data),
            detail="cannot_decode",
        )


def raster_image_from_path(path: Path) -> RasterResult:
    """In-process decode from a filesystem path (worker)."""
    from PIL import Image, UnidentifiedImageError

    _prepare_pil(Image)
    source_bytes: int | None = None
    try:
        source_bytes = path.stat().st_size
        if source_bytes <= 0:
            return RasterResult(status="empty", source_bytes=0)
        with Image.open(path) as img:
            return _raster_opened(img, source_bytes=source_bytes)
    except Image.DecompressionBombError:
        return RasterResult(
            status="unreadable",
            source_bytes=source_bytes,
            detail="pixels_exceeded",
        )
    except (UnidentifiedImageError, OSError, ValueError):
        return RasterResult(
            status="unreadable",
            source_bytes=source_bytes,
            detail="cannot_decode",
        )


def too_large_result(exc: BaseException) -> RasterResult:
    from agentcore.workspace.file_kind import parse_too_large_size

    return RasterResult(
        status="too_large",
        source_bytes=parse_too_large_size(str(exc)),
        detail="too_large",
    )


def raster_fail_note(result: RasterResult) -> str:
    """Model-facing fact. No next-tool recipe."""
    if result.status == "empty":
        return "工作区图片为空，没有像素可发。"
    if result.status == "too_large":
        return "源文件超过识图摄入顶，本回合未把像素发给主模型。"
    if result.status == "timeout":
        return "识图预处理超时，本回合未把像素发给主模型。"
    return "无法解码为图片，本回合未把像素发给主模型。"


async def raster_image_file(path: Path, *, timeout: float | None = None) -> RasterResult:
    """Killable child opens ``path``. Parent must not ``Image.open`` first."""
    return await _raster_isolated(source_path=path, data=None, timeout=timeout)


async def raster_image_bytes(data: bytes, *, timeout: float | None = None) -> RasterResult:
    """Killable child reads stdin bytes (Local IPC already slurped the file)."""
    if not data:
        return RasterResult(status="empty", source_bytes=0)
    return await _raster_isolated(source_path=None, data=data, timeout=timeout)


def _prepare_pil(pil_image: object) -> None:
    pil_image.MAX_IMAGE_PIXELS = VISION_MAX_DECODE_PIXELS  # type: ignore[attr-defined]


def _raster_opened(img: object, *, source_bytes: int | None) -> RasterResult:
    from PIL import Image, ImageOps

    if not isinstance(img, Image.Image):
        return RasterResult(status="unreadable", source_bytes=source_bytes, detail="cannot_decode")
    with contextlib.suppress(EOFError):
        img.seek(0)
    try:
        transposed = ImageOps.exif_transpose(img)
        if transposed is not None:
            img = transposed
    except (OSError, ValueError, SyntaxError):
        pass
    source_width, source_height = img.size
    pixels = source_width * source_height
    if pixels > VISION_MAX_DECODE_PIXELS:
        return RasterResult(
            status="unreadable",
            source_width=source_width,
            source_height=source_height,
            source_bytes=source_bytes,
            detail="pixels_exceeded",
        )
    max_edge = choose_max_edge(source_width, source_height)
    if (img.format or "").upper() == "JPEG":
        with contextlib.suppress(Exception):
            img.draft("RGB", (max_edge, max_edge))
    rgb = _flatten_rgb(img)
    fitted = fit_rgb(rgb, max_edge=max_edge)
    if not isinstance(fitted, Image.Image):
        return RasterResult(
            status="unreadable",
            source_width=source_width,
            source_height=source_height,
            source_bytes=source_bytes,
            detail="encode_failed",
        )
    buf = io.BytesIO()
    fitted.save(buf, format="JPEG", quality=VISION_JPEG_QUALITY, optimize=True)
    jpeg = buf.getvalue()
    if not jpeg:
        return RasterResult(
            status="unreadable",
            source_width=source_width,
            source_height=source_height,
            source_bytes=source_bytes,
            detail="encode_failed",
        )
    width, height = fitted.size
    return RasterResult(
        status="ok",
        jpeg=jpeg,
        width=width,
        height=height,
        source_width=source_width,
        source_height=source_height,
        source_bytes=source_bytes,
        max_edge=max_edge,
    )


def _flatten_rgb(img: object) -> object:
    from PIL import Image

    if not isinstance(img, Image.Image):
        return img
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.split()[-1])
        return background
    return img.convert("RGB")


def _spawn_group_kwargs() -> dict:
    return {} if _IS_WINDOWS else {"start_new_session": True}


def _reap_tree_sync(process: subprocess.Popen[bytes], pid: int) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        with contextlib.suppress(Exception):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
    else:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.wait(timeout=5)


def _raster_worker_argv(*, source_path: Path | None) -> list[str]:
    argv = [sys.executable, "-m", "agentcore.workspace.image_raster", "--raster-worker"]
    if source_path is not None:
        argv.extend(["--raster-path", str(source_path)])
    return argv


def _run_raster_subprocess(
    data: bytes | None,
    timeout: float,
    *,
    holder: dict | None = None,
    source_path: Path | None = None,
) -> RasterResult:
    meta_fd, meta_path = tempfile.mkstemp(suffix=".raster.json")
    os.close(meta_fd)
    jpeg_fd, jpeg_path = tempfile.mkstemp(suffix=".jpg")
    os.close(jpeg_fd)
    proc: subprocess.Popen[bytes] | None = None
    path_mode = source_path is not None
    try:
        env = os.environ.copy()
        env[_RASTER_META_ENV] = meta_path
        env[_RASTER_JPEG_ENV] = jpeg_path
        proc = subprocess.Popen(
            _raster_worker_argv(source_path=source_path),
            stdin=subprocess.DEVNULL if path_mode else subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            env=env,
            **_spawn_group_kwargs(),
        )
        if holder is not None:
            holder["proc"] = proc
        try:
            _, stderr = proc.communicate(
                input=None if path_mode else data, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            _reap_tree_sync(proc, proc.pid)
            return RasterResult(status="timeout", detail="raster_timeout")
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace").strip()[:120]
            logger.warning(
                "workspace.vision_raster_failed",
                returncode=proc.returncode,
                error=err,
            )
            return RasterResult(
                status="unreadable",
                detail=f"raster_worker:{proc.returncode}",
            )
        payload = json.loads(Path(meta_path).read_text(encoding="utf-8"))
        return _result_from_worker_payload(payload, jpeg_path=jpeg_path)
    except Exception as e:
        if proc is not None and proc.poll() is None:
            _reap_tree_sync(proc, proc.pid)
        logger.warning(
            "workspace.vision_raster_spawn_failed",
            error=str(e),
            error_type=type(e).__name__,
        )
        return RasterResult(status="unreadable", detail=f"raster_spawn:{type(e).__name__}")
    finally:
        with contextlib.suppress(OSError):
            os.unlink(meta_path)
        with contextlib.suppress(OSError):
            os.unlink(jpeg_path)


def _result_from_worker_payload(payload: dict, *, jpeg_path: str) -> RasterResult:
    status = str(payload.get("status") or "unreadable")
    if status not in ("ok", "empty", "too_large", "unreadable", "timeout"):
        status = "unreadable"
    jpeg = b""
    if status == "ok":
        with contextlib.suppress(OSError):
            jpeg = Path(jpeg_path).read_bytes()
        if not jpeg:
            status = "unreadable"
    return RasterResult(
        status=status,  # type: ignore[arg-type]
        jpeg=jpeg,
        width=int(payload.get("width") or 0),
        height=int(payload.get("height") or 0),
        source_width=int(payload.get("source_width") or 0),
        source_height=int(payload.get("source_height") or 0),
        source_bytes=_optional_int(payload.get("source_bytes")),
        max_edge=int(payload.get("max_edge") or 0),
        detail=str(payload.get("detail") or ""),
    )


def _optional_int(raw: object) -> int | None:
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _result_to_worker_payload(result: RasterResult) -> dict:
    return {
        "status": result.status,
        "width": result.width,
        "height": result.height,
        "source_width": result.source_width,
        "source_height": result.source_height,
        "source_bytes": result.source_bytes,
        "max_edge": result.max_edge,
        "detail": result.detail,
    }


async def _raster_isolated(
    *,
    source_path: Path | None,
    data: bytes | None,
    timeout: float | None,
) -> RasterResult:
    budget = VISION_TIMEOUT_SECONDS if timeout is None else timeout
    holder: dict = {}
    loop = asyncio.get_running_loop()

    def blocking() -> RasterResult:
        return _run_raster_subprocess(
            data,
            budget,
            holder=holder,
            source_path=source_path,
        )

    fut = loop.run_in_executor(None, blocking)
    try:
        return await fut
    except asyncio.CancelledError:
        proc = holder.get("proc")
        if proc is not None:
            await asyncio.to_thread(_reap_tree_sync, proc, proc.pid)
        with contextlib.suppress(Exception):
            await fut
        raise


def _raster_worker_main(argv: list[str]) -> int:
    source_path: Path | None = None
    args = list(argv)
    while args:
        token = args.pop(0)
        if token == "--raster-worker":
            continue
        if token == "--raster-path" and args:
            source_path = Path(args.pop(0))
            continue
        return 2
    meta_path = os.environ.get(_RASTER_META_ENV, "")
    jpeg_path = os.environ.get(_RASTER_JPEG_ENV, "")
    if not meta_path:
        sys.stderr.write("missing raster meta path\n")
        return 2
    if source_path is not None:
        result = raster_image_from_path(source_path)
    else:
        result = raster_image_payload(sys.stdin.buffer.read())
    Path(meta_path).write_text(
        json.dumps(_result_to_worker_payload(result), ensure_ascii=False),
        encoding="utf-8",
    )
    if result.status == "ok" and result.jpeg and jpeg_path:
        Path(jpeg_path).write_bytes(result.jpeg)
    return 0


def _cli_main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--raster-worker":
        return _raster_worker_main(args)
    sys.stderr.write("internal vision raster worker\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(_cli_main())
