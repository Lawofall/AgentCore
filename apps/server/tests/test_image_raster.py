"""Vision ingest: downsample, document-scan edge, decode isolation."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from agentcore.workspace.image_raster import (
    VISION_DOC_MAX_EDGE,
    VISION_MAX_EDGE,
    choose_max_edge,
    raster_image_bytes,
    raster_image_from_path,
    raster_image_payload,
)


def _rgb_png(width: int, height: int) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_choose_max_edge_ordinary_photo():
    assert choose_max_edge(3000, 2000) == VISION_MAX_EDGE


def test_choose_max_edge_tall_or_huge_scan():
    assert choose_max_edge(1000, 5000) == VISION_DOC_MAX_EDGE
    assert choose_max_edge(9912, 14032) == VISION_DOC_MAX_EDGE


def test_payload_downscales_long_edge():
    result = raster_image_payload(_rgb_png(3000, 2000))
    assert result.status == "ok"
    assert result.max_edge == VISION_MAX_EDGE
    assert max(result.width, result.height) == VISION_MAX_EDGE
    assert result.jpeg[:3] == b"\xff\xd8\xff"


def test_payload_doc_scan_uses_2048():
    result = raster_image_payload(_rgb_png(1000, 5000))
    assert result.status == "ok"
    assert result.max_edge == VISION_DOC_MAX_EDGE
    assert max(result.width, result.height) == VISION_DOC_MAX_EDGE


def test_payload_unreadable():
    result = raster_image_payload(b"not-an-image")
    assert result.status == "unreadable"
    assert not result.jpeg


def test_payload_empty():
    result = raster_image_payload(b"")
    assert result.status == "empty"


def test_from_path_roundtrip(tmp_path: Path):
    path = tmp_path / "shot.png"
    path.write_bytes(_rgb_png(16, 16))
    result = raster_image_from_path(path)
    assert result.status == "ok"
    assert result.source_width == 16
    assert result.jpeg[:3] == b"\xff\xd8\xff"


@pytest.mark.asyncio
async def test_isolated_bytes_roundtrip():
    result = await raster_image_bytes(_rgb_png(12, 8))
    assert result.status == "ok"
    assert result.jpeg[:3] == b"\xff\xd8\xff"
    assert result.width > 0
