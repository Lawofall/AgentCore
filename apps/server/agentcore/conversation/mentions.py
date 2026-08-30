"""Conversation-page ``agent_mentions`` persist helpers (soft prompt, not attachments).

Display-only chips stored beside ``messages.attachments``. Never mixed into
``MessageAttachment.kind``; never used as hard-route / force-delegate.
"""

from __future__ import annotations

_MAX_ITEMS = 10
_ID_MAX = 128
_ROLE_MAX = 200


def format_agent_mention_prompt(agent_mentions: list[dict] | None) -> str | None:
    """Render conversation-page Agent soft mentions into a prompt block.

    Soft hint only — does not force delegate / hard-route. Empty / missing → None
    so the turn stays byte-identical to today's no-mention assembly.
    """
    if not agent_mentions:
        return None
    lines: list[str] = []
    for raw in agent_mentions:
        if not isinstance(raw, dict):
            continue
        agent_id = str(raw.get("agent_id") or "").strip()
        role = str(raw.get("role") or "").strip()
        if not agent_id or not role:
            continue
        lines.append(f"- {role} (id={agent_id})")
    if not lines:
        return None
    return (
        "<队员点名>\n"
        "用户点名关注以下 Agent（软提示，非强制派单/非硬路由）：\n"
        + "\n".join(lines)
        + "\n</队员点名>"
    )


def to_stored_agent_mentions(raw: list | None) -> list[dict]:
    """Project in-flight mentions to the JSONB column (``{agent_id, role}`` only)."""
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or "").strip()
        role = str(item.get("role") or "").strip()
        if not agent_id or not role:
            continue
        if len(agent_id) > _ID_MAX or len(role) > _ROLE_MAX:
            continue
        out.append({"agent_id": agent_id, "role": role})
        if len(out) >= _MAX_ITEMS:
            break
    return out


def wire_agent_mentions(raw: list | None) -> list[dict] | None:
    """Sanitize for SSE / REST; empty → None so the key stays off the wire."""
    stored = to_stored_agent_mentions(raw)
    return stored or None


def resolve_interjection_mentions(
    payload: dict | None = None,
    stashed: dict | None = None,
) -> list[dict] | None:
    """Mentions ride the event payload when present; otherwise the process-local stash.

    Same lookup the coordination inject brief uses. Empty / missing → None.
    """
    for source in (payload, stashed):
        if not isinstance(source, dict):
            continue
        raw = source.get("agent_mentions")
        if isinstance(raw, list) and raw:
            stored = wire_agent_mentions(raw)
            if stored:
                return stored
    return None
