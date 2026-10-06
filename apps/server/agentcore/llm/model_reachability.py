"""Shared「this model works on this provider」check.

Used by BYOK connectivity tests (模型组合 slot ids) and model-profile save
warnings. Prefer ``list_models``; fall through to ``probe`` when the list is
empty, missing, or omits the model (e.g. Ark ``ep-`` endpoints). Save path
uses a softer policy so list fetch failures never block or slow a successful
save.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agentcore.core.errors import LLMAuthError, LLMError, LLMInsufficientBalanceError
from agentcore.llm.errors import is_auth_rejection

Reachability = Literal["ok", "error", "unknown"]
ListKind = Literal["ok", "hard_error", "soft_error"]


@dataclass(frozen=True)
class ModelListOutcome:
    """One ``GET /models`` attempt — reuse across several model checks."""

    kind: ListKind
    model_ids: tuple[str, ...] = ()
    message: str | None = None


@dataclass(frozen=True)
class CheckPolicy:
    """How inconclusive / list-hard outcomes map to reachability.

    ``not_in_list`` always probes when the upstream list is non-empty and omits
    the model — that is what separates Ark ``ep-`` (probe OK) from typos
    (probe fail). Soft / empty / hard list outcomes differ by caller:
    connectivity test probes or errors; save warns only when a definitive
    list-miss + probe fail is available.
    """

    on_list_hard_error: Literal["error", "unknown"] = "error"
    on_list_soft_or_empty: Literal["probe", "unknown"] = "probe"


CONNECTIVITY_POLICY = CheckPolicy()
SAVE_WARN_POLICY = CheckPolicy(
    on_list_hard_error="unknown",
    on_list_soft_or_empty="unknown",
)


async def fetch_model_list(provider: object) -> ModelListOutcome:
    """Best-effort ``list_models``; auth/balance → hard_error, other LLM → soft."""
    list_fn = getattr(provider, "list_models", None)
    if not callable(list_fn):
        return ModelListOutcome(kind="soft_error")
    try:
        model_ids = await list_fn()
    except (LLMAuthError, LLMInsufficientBalanceError) as e:
        return ModelListOutcome(kind="hard_error", message=str(e))
    except LLMError:
        return ModelListOutcome(kind="soft_error")
    return ModelListOutcome(kind="ok", model_ids=tuple(model_ids or ()))


async def check_model_reachable(
    provider: object,
    *,
    model: str,
    model_list: ModelListOutcome | None = None,
    policy: CheckPolicy = CONNECTIVITY_POLICY,
) -> tuple[Reachability, str | None]:
    """Return whether ``model`` is usable on ``provider`` under ``policy``.

    Prefers a successful non-empty list that contains ``model``. Otherwise
    probes chat when the policy allows (same ladder as the former
    ``_run_connectivity_test`` body).
    """
    outcome = model_list if model_list is not None else await fetch_model_list(provider)
    model_s = (model or "").strip()

    if outcome.kind == "hard_error":
        if policy.on_list_hard_error == "error":
            return "error", outcome.message
        return "unknown", None

    if outcome.kind == "ok" and outcome.model_ids and model_s in outcome.model_ids:
        return "ok", None

    # Soft list failure, empty list, or non-empty list missing model.
    list_inconclusive = outcome.kind == "soft_error" or (
        outcome.kind == "ok" and not outcome.model_ids
    )
    if list_inconclusive and policy.on_list_soft_or_empty == "unknown":
        return "unknown", None

    if not model_s:
        return "error", "模型名称不能为空"

    try:
        await provider.probe(model=model_s)  # type: ignore[attr-defined]
    except LLMError as e:
        rewritten = _connectivity_probe_auth_copy(
            e, model=model_s, list_outcome=outcome
        )
        return "error", rewritten if rewritten is not None else str(e)
    return "ok", None


def _connectivity_probe_auth_copy(
    exc: LLMError, *, model: str, list_outcome: ModelListOutcome
) -> str | None:
    """Replace probe 401/auth-403 copy using ``upstream_status``, never product text.

    A successful non-empty ``GET /models`` already proved the Key; a later probe
    401/403 then names the 模型组合 model. Soft / empty lists did not
    prove the Key — mention both Key and model. Balance, 404, 5xx, and non-auth
    403 keep the original sentence.
    """
    if isinstance(exc, LLMInsufficientBalanceError):
        return None
    status = exc.details.get("upstream_status")
    if status not in (401, 403):
        return None
    preview = exc.details.get("upstream_body_preview")
    body: bytes | str | None = preview if isinstance(preview, bytes | str) else None
    if not is_auth_rejection(status, body):
        return None
    if list_outcome.kind == "ok" and list_outcome.model_ids:
        return (
            f"模型「{model}」不被上游接受（不存在或无权）。"
            "当前 API Key 已能列出模型，请改「装配」中的该模型。"
        )
    return (
        f"请核对 API Key 与模型「{model}」。"
        "未能区分是密钥无效还是该模型不被上游接受。"
    )
