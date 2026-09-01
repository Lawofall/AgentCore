"""Write-side guards for document CRUD (AI memory cores vs user-owned entries).

Core memory leaves keep fixed store-relative names from ``memory.store`` —
偏好.md / 画像.md / 导航.md. ``DELETE /documents/{id}`` still refuses those rows
(the name is the load protocol). Clearing the *body* is a different path:
empty ``PUT /users/me/memory/files/{kind}`` drops the note so it leaves
injection; the file-page list keeps a placeholder. Apply-mode mutations for
any ``ai_maintained`` row are refused at the API layer (see
``api/routes/documents.py``).
"""

from __future__ import annotations

from agentcore.memory.store import (
    CORE_MEMORY_FILE,
    NAVIGATION_MEMORY_FILE,
    PREFERENCES_MEMORY_FILE,
)

# Fixed names of the always-injected AI memory cores (not on-demand 主题/* topics).
AI_CORE_MEMORY_NAMES: frozenset[str] = frozenset(
    {
        PREFERENCES_MEMORY_FILE,
        CORE_MEMORY_FILE,
        NAVIGATION_MEMORY_FILE,
    }
)


def is_ai_core_memory_leaf(*, name: str, ai_maintained: bool) -> bool:
    """True when this row is an AI-maintained core leaf (画像 / 偏好 / 导航).

    Theme notes (``主题/<slug>.md``) are also ``ai_maintained`` but are not cores —
    they remain deletable. User-owned docs that happen to share a core filename are
    not cores (``ai_maintained`` must be true).
    """
    return ai_maintained and name in AI_CORE_MEMORY_NAMES
