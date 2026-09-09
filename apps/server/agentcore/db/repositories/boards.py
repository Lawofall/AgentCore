"""AI 协作白板 (whiteboard) data access.

Boards are user-scoped: every read/write resolves the owner and a non-owner is
treated as absent (the route turns that into a 404 — IDOR-safe, 照 folders.py).
Scene writes are CAS-guarded on ``version`` (照 memory.py): a write whose baseline
no longer matches is reported as a conflict instead of clobbering.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.db.models import Board


class BoardRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(
        self,
        *,
        user_id: str,
        title: str,
    ) -> Board:
        board = Board(user_id=user_id, title=title, scene={}, version=1)
        self._session.add(board)
        await self._session.commit()
        await self._session.refresh(board)
        return board

    async def get_by_id(self, board_id: str, *, user_id: str) -> Board | None:
        """Owner-scoped fetch (non-owner / unknown id → None → route 404). ``user_id``
        mandatory so scoping is the structural default (SEC-002)."""
        result = await self._session.execute(
            select(Board).where(
                Board.id == board_id,
                Board.deleted_at.is_(None),
                Board.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_conversation_id(self, conversation_id: str, *, user_id: str) -> Board | None:
        """The board bound to a conversation, if any (AI协作白板.md §三 A / M2 反查).

        The run assembler calls this to decide whether a turn is a 白板会话 — if so it
        wires ``board_ops`` + a :class:`BoardChannel` for that board. Owner-scoped (a
        non-owner is treated as absent); ``user_id`` mandatory (SEC-002). Indexed on
        ``conversation_id``.
        """
        result = await self._session.execute(
            select(Board).where(
                Board.conversation_id == conversation_id,
                Board.deleted_at.is_(None),
                Board.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: str) -> Sequence[Board]:
        """A user's live boards, most-recently-updated first (the「白板」list order)."""
        result = await self._session.execute(
            select(Board)
            .where(Board.user_id == user_id, Board.deleted_at.is_(None))
            .order_by(Board.updated_at.desc())
        )
        return result.scalars().all()

    async def update_meta(
        self,
        board_id: str,
        *,
        user_id: str,
        title: str | None = None,
    ) -> Board | None:
        """Rename a board (scene untouched)."""
        board = await self.get_by_id(board_id, user_id=user_id)
        if not board:
            return None
        if title is not None:
            board.title = title
        await self._session.commit()
        await self._session.refresh(board)
        return board

    async def save_scene(
        self,
        board_id: str,
        *,
        user_id: str,
        scene: dict,
        baseline: int | None,
    ) -> tuple[Board | None, bool]:
        """CAS-write the scene; returns ``(board, conflict)``.

        - ``(None, False)`` → no such board for this user (404).
        - ``(board, True)`` → baseline no longer matches current version; the LIVE board
          is returned untouched so the caller can surface / reconcile (never clobbered).
        - ``(board, False)`` → written; ``version`` bumped, ``board`` refreshed.

        ``baseline=None`` writes unconditionally (e.g. forced「仍然覆盖」), mirroring the
        memory editor's escape hatch.
        """
        board = await self.get_by_id(board_id, user_id=user_id)
        if not board:
            return None, False
        if baseline is not None and baseline != board.version:
            return board, True
        board.scene = scene
        board.version += 1
        await self._session.commit()
        await self._session.refresh(board)
        return board, False

    async def attach_conversation(
        self, board_id: str, *, user_id: str, conversation_id: str
    ) -> Board | None:
        """Bind a board to its dedicated AI conversation (AI协作白板.md §三 A / M2).

        Called once, lazily, when the board first needs an AI thread. Idempotency is the
        caller's job (it checks ``board.conversation_id`` first); this only writes the link.
        Returns the refreshed board, or ``None`` if absent for this user.
        """
        board = await self.get_by_id(board_id, user_id=user_id)
        if not board:
            return None
        board.conversation_id = conversation_id
        await self._session.commit()
        await self._session.refresh(board)
        return board

    async def soft_delete(self, board_id: str, *, user_id: str) -> bool:
        board = await self.get_by_id(board_id, user_id=user_id)
        if not board:
            return False
        board.deleted_at = datetime.now()
        await self._session.commit()
        return True
