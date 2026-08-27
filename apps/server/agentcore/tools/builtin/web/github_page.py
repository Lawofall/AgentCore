"""GitHub HTML page → api.github.com fast path for ``read_url``.

``github.com/{owner}`` (profile / ``?tab=repositories``) is a JS shell over HTML;
``github.com/{owner}/{repo}`` (root / tree / blob) often times out or returns
login chrome. The REST API is smaller and exposes ``private`` / ``visibility``
so the model need not guess. Match failure or any API error returns ``None``
so the caller falls back to the existing HTML fetch.

Account PAT (G3 ``load_git_auth_for_user``) is attached when ``user_id`` has
credentials; never log the token. Unauthenticated API is still attempted.
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

import httpx

from agentcore.core.logging import get_logger

logger = get_logger(__name__)

_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
# First path segment that is never a user/org owning a repo page we care about.
_RESERVED_OWNERS = frozenset(
    {
        "settings",
        "login",
        "logout",
        "join",
        "signup",
        "session",
        "sessions",
        "marketplace",
        "explore",
        "topics",
        "collections",
        "events",
        "sponsors",
        "about",
        "pricing",
        "enterprise",
        "features",
        "security",
        "orgs",
        "organizations",
        "account",
        "notifications",
        "pulls",
        "issues",
        "codespaces",
        "copilot",
        "search",
        "new",
        "dashboard",
        "apps",
        "integrations",
        "site",
        "git-receive-pack",
        "git-upload-pack",
    }
)

# Profile homepage and the repositories tab; other ?tab= values stay on HTML.
_OWNER_LISTING_TABS = frozenset({"", "overview", "repositories"})
_OWNER_REPO_LIMIT = 30
_PAT_LOOKUP_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class _GithubOwnerPage:
    owner: str


@dataclass(frozen=True, slots=True)
class _GithubRepoPage:
    owner: str
    repo: str
    ref: str | None = None  # None → default branch (README API)


@dataclass(frozen=True, slots=True)
class _GithubBlobPage:
    owner: str
    repo: str
    ref: str
    path: str


GithubPage = _GithubOwnerPage | _GithubRepoPage | _GithubBlobPage


def _owner_listing_query_ok(query: str) -> bool:
    """True for profile / repositories tab; other ``?tab=`` values stay on HTML."""
    if not query:
        return True
    qs = parse_qs(query, keep_blank_values=True)
    tabs = qs.get("tab")
    if not tabs:
        return True
    tab = (tabs[0] or "").strip().lower()
    return tab in _OWNER_LISTING_TABS


def parse_github_page_url(url: str) -> GithubPage | None:
    """Parse GitHub owner / repo / tree / blob URLs.

    Owner pages: ``github.com/{owner}`` and ``?tab=repositories`` (user or org
    — distinguished at fetch time). Repo pages: root / tree / blob.

    Returns ``None`` for non-GitHub hosts, non-repo tabs (issues/PRs/stars/…),
    or malformed paths — caller should use the HTML path.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _GITHUB_HOSTS:
        return None
    parts = [p for p in (parsed.path or "").split("/") if p]
    if not parts:
        return None
    owner = parts[0]
    if owner.lower() in _RESERVED_OWNERS:
        return None

    if len(parts) == 1:
        if not _owner_listing_query_ok(parsed.query):
            return None
        return _GithubOwnerPage(owner=owner)

    repo = parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not repo:
        return None

    if len(parts) == 2:
        return _GithubRepoPage(owner=owner, repo=repo)

    kind = parts[2].lower()
    if kind == "tree":
        ref = parts[3] if len(parts) >= 4 else None
        return _GithubRepoPage(owner=owner, repo=repo, ref=ref)
    if kind == "blob":
        if len(parts) < 5:
            return None
        return _GithubBlobPage(
            owner=owner,
            repo=repo,
            ref=parts[3],
            path="/".join(parts[4:]),
        )
    return None


def _api_headers(*, token: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": "AgentCore/1.0 (+https://agentcore.dev)",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def _load_account_pat(user_id: str | None) -> str | None:
    """G3 account PAT or ``None``. Never log the return value."""
    if not user_id:
        return None
    from agentcore.workspace.git_credentials import load_git_auth_for_user

    try:
        auth = await asyncio.wait_for(
            load_git_auth_for_user(user_id), _PAT_LOOKUP_TIMEOUT
        )
    except Exception:  # noqa: BLE001 — fail-soft; unauthenticated API still runs
        return None
    if auth is None:
        return None
    token = (auth.token or "").strip()
    return token or None


def _fallback_detail(exc: BaseException, token: str | None) -> str:
    detail = str(exc)[:200]
    if token:
        detail = detail.replace(token, "***")
    return detail


def _decode_github_content(payload: dict[str, Any]) -> str | None:
    """Decode a contents/readme API body; ``None`` if missing or not base64 text."""
    encoding = (payload.get("encoding") or "").lower()
    content = payload.get("content")
    if encoding != "base64" or not isinstance(content, str) or not content.strip():
        return None
    try:
        raw = base64.b64decode(content, validate=False)
    except Exception:
        return None
    return raw.decode("utf-8", errors="replace")


def _format_repo_body(
    meta: dict[str, Any],
    *,
    readme_text: str | None,
    readme_path: str | None,
    max_chars: int,
) -> str:
    owner = meta.get("owner")
    owner_login = owner.get("login", "") if isinstance(owner, dict) else ""
    full_name = meta.get("full_name") or f"{owner_login}/{meta.get('name', '')}"
    visibility = meta.get("visibility") or (
        "private" if meta.get("private") else "public"
    )
    lines = [
        f"repository: {full_name}",
        f"private: {json.dumps(bool(meta.get('private')))}",
        f"visibility: {visibility}",
        f"default_branch: {meta.get('default_branch') or ''}",
    ]
    desc = (meta.get("description") or "").strip()
    if desc:
        lines.append(f"description: {desc}")
    if meta.get("archived"):
        lines.append("archived: true")
    if meta.get("fork"):
        lines.append("fork: true")
    if meta.get("html_url"):
        lines.append(f"html_url: {meta['html_url']}")
    body = "\n".join(lines)
    if readme_text is not None:
        label = readme_path or "README"
        body = f"{body}\n\n--- {label} ---\n{readme_text}"
    return body[:max_chars]


def _format_blob_body(
    *,
    owner: str,
    repo: str,
    ref: str,
    path: str,
    file_text: str,
    max_chars: int,
) -> str:
    header = "\n".join(
        [
            f"repository: {owner}/{repo}",
            f"ref: {ref}",
            f"path: {path}",
        ]
    )
    return f"{header}\n\n--- file ---\n{file_text}"[:max_chars]


def _format_owner_body(
    *,
    owner: str,
    kind: str,
    profile: dict[str, Any],
    repos: list[dict[str, Any]],
    max_chars: int,
) -> str:
    html_url = profile.get("html_url") or f"https://github.com/{owner}"
    cap_note = (
        f" (capped at {_OWNER_REPO_LIMIT})" if len(repos) >= _OWNER_REPO_LIMIT else ""
    )
    lines = [
        f"profile: {owner}",
        f"type: {kind}",
        f"html_url: {html_url}",
        f"repositories: {len(repos)}{cap_note}",
    ]
    for repo in repos:
        full_name = repo.get("full_name") or ""
        lines.append("")
        lines.append(f"- {full_name}")
        desc = (repo.get("description") or "").strip()
        if desc:
            lines.append(f"  description: {desc}")
        branch = repo.get("default_branch")
        if branch:
            lines.append(f"  default_branch: {branch}")
        repo_html = repo.get("html_url")
        if repo_html:
            lines.append(f"  html_url: {repo_html}")
        if "private" in repo:
            lines.append(f"  private: {json.dumps(bool(repo.get('private')))}")
    return "\n".join(lines)[:max_chars]


async def try_fetch_github_page(
    client: httpx.AsyncClient,
    url: str,
    max_chars: int,
    *,
    safe_request: Any,
    user_id: str | None = None,
) -> tuple[str, str, str] | None:
    """Fetch via api.github.com when ``url`` is an owner / repo / tree / blob page.

    Returns ``(title, text, description)`` on success, else ``None`` (fall back
    to HTML). ``safe_request`` is ``read_url._safe_request`` (SSRF + breaker).
    Account PAT is used when ``user_id`` has G3 credentials; missing PAT still
    tries the unauthenticated API.
    """
    page = parse_github_page_url(url)
    if page is None:
        return None
    token = await _load_account_pat(user_id)
    headers = _api_headers(token=token)
    try:
        if isinstance(page, _GithubOwnerPage):
            return await _fetch_owner(client, page, max_chars, safe_request, headers)
        if isinstance(page, _GithubBlobPage):
            return await _fetch_blob(client, page, max_chars, safe_request, headers)
        return await _fetch_repo(client, page, max_chars, safe_request, headers)
    except Exception as e:
        logger.info(
            "tool.read_url_github_api_fallback",
            url=url[:200],
            error=type(e).__name__,
            detail=_fallback_detail(e, token),
        )
        return None


async def _fetch_owner(
    client: httpx.AsyncClient,
    page: _GithubOwnerPage,
    max_chars: int,
    safe_request: Any,
    headers: dict[str, str],
) -> tuple[str, str, str] | None:
    login = quote(page.owner)
    profile_url = f"https://api.github.com/users/{login}"
    profile_resp = await safe_request(client, "GET", profile_url, headers=headers)
    if profile_resp.status_code != 200:
        return None
    profile = profile_resp.json()
    if not isinstance(profile, dict):
        return None
    profile_type = str(profile.get("type") or "User")
    is_org = profile_type.lower() == "organization"
    kind = "organization" if is_org else "user"
    if is_org:
        repos_url = (
            f"https://api.github.com/orgs/{login}/repos"
            f"?per_page={_OWNER_REPO_LIMIT}&sort=updated"
        )
    else:
        repos_url = (
            f"https://api.github.com/users/{login}/repos"
            f"?per_page={_OWNER_REPO_LIMIT}&sort=updated"
        )
    repos_resp = await safe_request(client, "GET", repos_url, headers=headers)
    if repos_resp.status_code != 200:
        return None
    raw_repos = repos_resp.json()
    if not isinstance(raw_repos, list):
        return None
    repos = [r for r in raw_repos if isinstance(r, dict) and r.get("full_name")]
    title = str(profile.get("login") or page.owner)
    text = _format_owner_body(
        owner=title, kind=kind, profile=profile, repos=repos, max_chars=max_chars
    )
    description = (
        (profile.get("bio") or profile.get("description") or "").strip()
        or f"{len(repos)} repositories"
    )
    return title, text, description


async def _fetch_repo(
    client: httpx.AsyncClient,
    page: _GithubRepoPage,
    max_chars: int,
    safe_request: Any,
    headers: dict[str, str],
) -> tuple[str, str, str] | None:
    meta_url = f"https://api.github.com/repos/{quote(page.owner)}/{quote(page.repo)}"
    resp = await safe_request(client, "GET", meta_url, headers=headers)
    if resp.status_code != 200:
        return None
    meta = resp.json()
    if not isinstance(meta, dict):
        return None

    readme_text: str | None = None
    readme_path: str | None = None
    readme_url = f"{meta_url}/readme"
    if page.ref:
        readme_url = f"{readme_url}?ref={quote(page.ref)}"
    readme_resp = await safe_request(client, "GET", readme_url, headers=headers)
    if readme_resp.status_code == 200:
        readme_payload = readme_resp.json()
        if isinstance(readme_payload, dict):
            decoded = _decode_github_content(readme_payload)
            if decoded is not None:
                readme_text = decoded
                path = readme_payload.get("path")
                readme_path = path if isinstance(path, str) else None

    full_name = meta.get("full_name") or f"{page.owner}/{page.repo}"
    title = str(full_name)
    text = _format_repo_body(
        meta, readme_text=readme_text, readme_path=readme_path, max_chars=max_chars
    )
    description = (meta.get("description") or "").strip()
    return title, text, description


async def _fetch_blob(
    client: httpx.AsyncClient,
    page: _GithubBlobPage,
    max_chars: int,
    safe_request: Any,
    headers: dict[str, str],
) -> tuple[str, str, str] | None:
    # Encode path segments but keep slashes (contents API uses path with /).
    enc_path = "/".join(quote(seg) for seg in page.path.split("/"))
    contents_url = (
        f"https://api.github.com/repos/{quote(page.owner)}/{quote(page.repo)}"
        f"/contents/{enc_path}?ref={quote(page.ref)}"
    )
    resp = await safe_request(client, "GET", contents_url, headers=headers)
    if resp.status_code != 200:
        return None
    payload = resp.json()
    # Directory listing is a list — not a file blob; fall back to HTML.
    if not isinstance(payload, dict):
        return None
    if payload.get("type") == "dir" or "content" not in payload:
        return None
    decoded = _decode_github_content(payload)
    if decoded is None:
        return None
    title = f"{page.owner}/{page.repo}/{page.path}"
    text = _format_blob_body(
        owner=page.owner,
        repo=page.repo,
        ref=page.ref,
        path=page.path,
        file_text=decoded,
        max_chars=max_chars,
    )
    # Lead of the file for citation snippet.
    description = re.sub(r"\s+", " ", decoded).strip()[:200]
    return title, text, description
