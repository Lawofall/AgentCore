"""Favicon proxy route — reliable site icons for the source / citation UI.

The desktop renderer points its ``<img>`` at this endpoint instead of fetching
``https://{site}/favicon.ico`` directly: a China-hosted client frequently fails
the *source* site's TLS handshake (many ``*.gov.cn`` sites serve mismatched /
self-signed certs), so favicons silently fall back to letter chips. The server —
reachable to those sites — fetches the icon with cert verification relaxed (an
icon is cosmetic, low-trust), discovers the real icon from the page's
``<link rel="icon">`` when ``/favicon.ico`` is missing, and caches the bytes
in-process (with a negative cache for known misses) so repeated cards / turns
don't refetch.

Security:
- **SSRF** — every fetched URL (and each redirect hop) is run through the same
  private-IP guard as ``web_fetch`` (:func:`agentcore.core.net.is_safe_url`, the
  shared definition), so the proxy can't be used to reach internal hosts / cloud
  metadata.
- **Isolated from the egress breaker** — favicon fetches deliberately do NOT use
  ``web_fetch``'s shared per-host circuit breaker; a site whose *icon* fails must
  not trip the breaker that gates the agent's actual page reads of that host.
- **Public, but bounded** — no auth (an ``<img>`` can't carry the session
  cookie cross-origin), response capped + restricted to image bytes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import APIRouter, Query
from fastapi.responses import Response

from agentcore.core.logging import get_logger
from agentcore.core.net import (
    PinnedIPTransport,
    describe_net_error,
    is_safe_url,
    outbound_async_client,
    web_timeout,
)

logger = get_logger(__name__)

router = APIRouter(tags=["favicon"])

_MAX_BYTES = 256 * 1024  # icons are tiny; reject anything page-sized
_MAX_REDIRECTS = 4
_MAX_HTML_SNIFF = 64 * 1024  # only the <head> is needed to find <link rel=icon>
_CACHE_TTL = 24 * 3600  # a resolved icon is stable for a day
_NEG_TTL = 6 * 3600  # remember misses so failing sites fast-404 (no refetch storm)
_CACHE_MAX = 1024
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
}

# Magic-byte → media type. Many servers mislabel ``favicon.ico`` (e.g. as
# ``text/plain``), so we sniff the bytes rather than trust Content-Type.
_IMAGE_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\x01\x00", "image/x-icon"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"BM", "image/bmp"),
)


@dataclass
class _Entry:
    expires: float
    data: bytes | None  # None = negative cache (known miss)
    media_type: str


_cache: dict[str, _Entry] = {}


def _cache_get(domain: str) -> _Entry | None:
    entry = _cache.get(domain)
    if entry is None:
        return None
    if entry.expires <= time.monotonic():
        _cache.pop(domain, None)
        return None
    return entry


def _cache_put(domain: str, data: bytes | None, media_type: str, ttl: float) -> None:
    # Prune expired then oldest-first (dict keeps insertion order) to stay bounded.
    if len(_cache) >= _CACHE_MAX:
        now = time.monotonic()
        for key in [k for k, v in _cache.items() if v.expires <= now]:
            _cache.pop(key, None)
        while len(_cache) >= _CACHE_MAX:
            _cache.pop(next(iter(_cache)), None)
    _cache[domain] = _Entry(time.monotonic() + ttl, data, media_type)


def _normalize_domain(raw: str) -> str | None:
    """Reduce an input (host or full URL) to a bare lowercased hostname, or None.

    Accepts ``example.com`` and ``https://example.com/x`` alike; rejects empty,
    over-long, or obviously non-host strings (a space, a path-only value).
    """
    value = (raw or "").strip().lower()
    if not value:
        return None
    host = urlparse(value).hostname if "//" in value else value.split("/", 1)[0]
    host = (host or "").strip().rstrip(".")
    if not host or len(host) > 253 or " " in host or "." not in host:
        return None
    return host


def _sniff_media_type(data: bytes, header_ct: str) -> str | None:
    """Resolve a usable image media type, or None when the bytes aren't an image.

    Trusts a magic-byte match first; SVG (text) is allowed when the header says so
    or the body looks like SVG; an explicit ``image/*`` header is the last resort.
    HTML (a 404 page returned as 200) yields None.
    """
    head = data[:64].lstrip()
    if head[:5].lower() == b"<html" or head[:14].lower() == b"<!doctype html":
        return None
    for magic, media_type in _IMAGE_MAGIC:
        if data.startswith(magic):
            return media_type
    is_svg = (
        header_ct.startswith("image/svg")
        or head[:4].lower() == b"<svg"
        or (head[:5].lower() == b"<?xml" and b"<svg" in data[:512].lower())
    )
    if is_svg:
        return "image/svg+xml"
    if header_ct.startswith("image/"):
        return header_ct.split(";", 1)[0].strip()
    return None


class _IconLinkParser(HTMLParser):
    """Collect ``<link rel=…icon… href=…>`` hrefs from a page's head."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if "icon" in a.get("rel", "").lower() and a.get("href", "").strip():
            self.hrefs.append(a["href"].strip())


async def _read_image(resp: httpx.Response) -> tuple[bytes, str] | None:
    """Validate a response as a bounded image; return ``(bytes, media_type)`` or None."""
    if resp.status_code != 200:
        return None
    clen = resp.headers.get("content-length")
    if clen and clen.isdigit() and int(clen) > _MAX_BYTES:
        return None
    data = resp.content
    if not data or len(data) > _MAX_BYTES:
        return None
    media_type = _sniff_media_type(data, resp.headers.get("content-type", "").lower())
    return (data, media_type) if media_type else None


async def _fetch_checked(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    """GET ``url`` re-checking SSRF on every redirect hop (client must not auto-follow).

    Returns the final response, or None if a hop is blocked / too many redirects.
    Unlike ``web_fetch._safe_request`` this does NOT touch the shared egress breaker.
    """
    request = client.build_request("GET", url, headers=_BROWSER_HEADERS)
    for _ in range(_MAX_REDIRECTS + 1):
        if not await is_safe_url(str(request.url)):
            return None
        resp = await client.send(request)
        nxt = resp.next_request
        if resp.is_redirect and nxt is not None:
            await resp.aclose()
            request = nxt
            continue
        return resp
    return None


async def _resolve_favicon(domain: str) -> tuple[bytes, str] | None:
    """Best-effort fetch of ``domain``'s icon: ``/favicon.ico`` then ``<link rel=icon>``.

    TLS verification is relaxed on purpose — an icon is cosmetic and many target
    sites have invalid certs; the SSRF guard still bounds *which* hosts we reach.
    """
    # verify=False: cosmetic asset, and the whole point is to tolerate the bad
    # certs that defeat the client's direct fetch. follow_redirects=False so each
    # hop is SSRF-rechecked in _fetch_checked. PinnedIPTransport closes the
    # DNS-rebinding TOCTOU (the relaxed verify rides the inner transport, since a
    # custom transport makes the client-level ``verify`` kwarg a no-op).
    async with outbound_async_client(
        timeout=web_timeout(8.0),
        follow_redirects=False,
        transport=PinnedIPTransport(verify=False),
    ) as client:
        try:
            resp = await _fetch_checked(client, f"https://{domain}/favicon.ico")
            if resp is not None:
                icon = await _read_image(resp)
                if icon is not None:
                    return icon

            page = await _fetch_checked(client, f"https://{domain}/")
            if page is None or page.status_code != 200:
                return None
            parser = _IconLinkParser()
            parser.feed(page.text[:_MAX_HTML_SNIFF])
            base = str(page.url)
            for href in parser.hrefs:
                resp = await _fetch_checked(client, urljoin(base, href))
                if resp is not None:
                    icon = await _read_image(resp)
                    if icon is not None:
                        return icon
        except (httpx.HTTPError, ValueError) as e:
            logger.debug("favicon.fetch_failed", domain=domain, error=describe_net_error(e))
        return None


@router.get("/favicon")
async def get_favicon(domain: str = Query(..., min_length=1, max_length=300)) -> Response:
    """Proxy a site's favicon by hostname; 404 when none can be resolved.

    A 404 is normal — the client ``<img>`` falls back to a letter chip / hides — so
    failures are cached negatively to avoid re-fetching a known-bad site every render.
    """
    host = _normalize_domain(domain)
    if host is None:
        return Response(status_code=404)

    cached = _cache_get(host)
    if cached is not None:
        if cached.data is None:
            return Response(status_code=404)
        return Response(
            content=cached.data,
            media_type=cached.media_type,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    icon = await _resolve_favicon(host)
    if icon is None:
        _cache_put(host, None, "", _NEG_TTL)
        return Response(status_code=404)

    data, media_type = icon
    _cache_put(host, data, media_type, _CACHE_TTL)
    return Response(
        content=data,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
