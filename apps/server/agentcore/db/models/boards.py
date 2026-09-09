"""AI 协作白板 (collaborative whiteboard) model.

A board is a spatial-JSON canvas (self-built engine scene) owned by a user.
Boards are account-scoped — they do not file under a folder (否决 board ∈ folder).
The leftover ``folder_id`` column is unread and not written.

The scene is the canonical model (空间 JSON 为真相): a single ``scene`` JSONB blob
holds elements / positions / arrows / groups / freehand. S3 offload + image-file
externalization (scene_blob_ref) is DEFERRED for v1 — scenes stay inline in Postgres
(text/shape scenes are small; TOAST covers occasional large ones). ``version``
is the CAS counter: a write must present a matching baseline or it is reported as a
conflict (照 memory.py), so a stale device/tab can never silently clobber.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from agentcore.db.base import Base

from ._helpers import _new_uuid


class Board(Base):
    __tablename__ = "boards"

    id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(PG_UUID(as_uuid=False), index=True)
    # Leftover unread column (once optional filing). Not written, not returned, not
    # cleared on folder delete. Kept to avoid a migration; do not resume writing it.
    folder_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), index=True, nullable=True
    )
    # Dedicated AI conversation for this board (AI协作白板.md §三 A 绑定 / M2): the run
    # the board's AI ops + 团队 work happen in, lazily created on first AI use. NULL =
    # this board has no AI thread yet. App-level FK (no DB constraint, per repo convention).
    conversation_id: Mapped[str | None] = mapped_column(
        PG_UUID(as_uuid=False), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False, server_default=text("''"))
    # Canonical scene (self-built engine JSON: schemaVersion + elements[]). Empty object for a
    # brand-new board; the client restores an empty scene to a usable canvas.
    scene: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    # CAS version: incremented on every scene write. A write whose baseline != this is a
    # conflict (returned, not applied) — guards multi-device / multi-tab clobbering.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=datetime.now
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
