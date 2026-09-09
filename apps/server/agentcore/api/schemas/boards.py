"""AI 协作白板 (whiteboard) request/response schemas.

The list/meta surface (``BoardSummary``) omits the scene so the「白板」list stays light;
``BoardDetail`` carries the full scene for the canvas. Scene writes are CAS-guarded:
the client sends the ``baseline`` version it edited from and gets back the new version,
or — on a stale baseline — ``conflict=True`` plus the live board to reconcile against.
"""

from datetime import datetime

from pydantic import BaseModel


class CreateBoardRequest(BaseModel):
    # None → defaulted server-side to「未命名白板」; the canvas can rename later.
    title: str | None = None


class UpdateBoardRequest(BaseModel):
    """Rename a board (scene is written via the dedicated scene endpoint)."""

    title: str | None = None


class BoardSummary(BaseModel):
    id: str
    title: str
    # The board's dedicated AI conversation, or None until first AI use (AI协作白板.md §三 A).
    # The canvas reads it to know whether an AI thread already exists; it calls the bind
    # endpoint to mint one on demand.
    conversation_id: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class BoardDetail(BoardSummary):
    """A board plus its full scene (the canvas load payload)."""

    scene: dict


class BoardSceneWriteRequest(BaseModel):
    scene: dict
    # The version the edit was based on. None writes unconditionally (forced overwrite);
    # a non-null value that no longer matches the live board → conflict (not applied).
    baseline: int | None = None


class BoardWriteResult(BaseModel):
    ok: bool
    version: int
    conflict: bool = False
    # The live board, populated only on conflict so the client can reconcile without a
    # second round trip (the scene it tried to clobber over).
    board: BoardDetail | None = None


class BoardConversationResponse(BaseModel):
    """The board's dedicated AI conversation id (existing or just-created)."""

    conversation_id: str
