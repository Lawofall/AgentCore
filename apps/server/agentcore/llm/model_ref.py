"""Product-facing catalog identity (one string).

Exact handles — reserved ``@`` prefix so they cannot collide with upstream
model ids that already contain ``/`` (OpenRouter-style ``openai/gpt-4o``)::

    @platform/{model_id}
    @byok/{provider_id}/{model_id}

``model_id`` may contain slashes. ``provider_id`` must not (UUID / short test ids).

Anything not starting with ``@`` is a human mention for catalog disambiguation.
Unprefixed router keys (``platform/{id}``, ``{provider_id}/{id}``) are *not*
accepted as handles — they collide with real model ids.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PLATFORM_REF_PREFIX = "@platform/"
BYOK_REF_PREFIX = "@byok/"

ParseKind = Literal["empty", "ref", "mention", "bad_ref"]


@dataclass(frozen=True)
class ModelRefParse:
    """Result of :func:`parse_model_input`."""

    kind: ParseKind
    origin: str = ""
    model: str = ""
    provider_id: str = ""
    error: str = ""


def format_model_ref(
    origin: str,
    model: str,
    provider_id: str | None = None,
) -> str:
    """Encode a resolved catalog row. Empty / incomplete → empty string."""
    mid = (model or "").strip()
    org = (origin or "").strip().lower()
    if not mid or org not in ("platform", "byok"):
        return ""
    if org == "platform":
        return f"{PLATFORM_REF_PREFIX}{mid}"
    pid = (provider_id or "").strip()
    if not pid:
        return ""
    return f"{BYOK_REF_PREFIX}{pid}/{mid}"


def parse_model_input(raw: str) -> ModelRefParse:
    """Classify one product ``model`` field: empty / exact @ref / mention / malformed @."""
    text = (raw or "").strip()
    if not text:
        return ModelRefParse(kind="empty")
    lower = text.lower()
    if lower.startswith(PLATFORM_REF_PREFIX):
        model = text[len(PLATFORM_REF_PREFIX) :].strip()
        if not model:
            return ModelRefParse(
                kind="bad_ref",
                error="目录身份 @platform/ 后须接模型 id。",
            )
        return ModelRefParse(kind="ref", origin="platform", model=model)
    if lower.startswith(BYOK_REF_PREFIX):
        rest = text[len(BYOK_REF_PREFIX) :]
        provider_id, sep, model = rest.partition("/")
        provider_id = provider_id.strip()
        model = model.strip()
        if not sep or not provider_id or not model:
            return ModelRefParse(
                kind="bad_ref",
                error="目录身份 @byok/{provider_id}/{model} 须同时含服务商 id 与模型 id。",
            )
        if "/" in provider_id:
            return ModelRefParse(
                kind="bad_ref",
                error="目录身份 @byok 的 provider_id 不能含 /。",
            )
        return ModelRefParse(
            kind="ref", origin="byok", model=model, provider_id=provider_id
        )
    if text.startswith("@"):
        return ModelRefParse(
            kind="bad_ref",
            error=(
                "目录身份须为 @platform/{model} 或 @byok/{provider_id}/{model}；"
                "其它 @ 前缀不是合法句柄。"
            ),
        )
    return ModelRefParse(kind="mention", model=text)
