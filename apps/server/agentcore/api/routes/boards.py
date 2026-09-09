"""AI 协作白板 (whiteboard) CRUD + scene write routes (AI协作白板.md §七/§九 M1).

Boards are user-scoped: every route resolves the authenticated user and a non-owner
receives 404 (IDOR-safe, 照 folders.py). The scene write is CAS-guarded on ``version``
(照 memory.py): a stale baseline is reported as a conflict carrying the live board,
never a blind overwrite. The board lives in the desktop, so auth is the same credentialed
session as the rest of the app (no new gate).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agentcore.api.dependencies import AuthUser, get_board_repo, get_db
from agentcore.api.schemas import (
    BoardConversationResponse,
    BoardDetail,
    BoardSceneWriteRequest,
    BoardSummary,
    BoardWriteResult,
    CreateBoardRequest,
    StatusResponse,
    UpdateBoardRequest,
)
from agentcore.core.errors import NotFoundError
from agentcore.db.repositories import BoardRepository, ConversationRepository

router = APIRouter(prefix="/boards", tags=["boards"])

# Default name for a board created without a title (the canvas can rename later).
_DEFAULT_TITLE = "未命名白板"


@router.post("", response_model=BoardSummary, status_code=201)
async def create_board(
    body: CreateBoardRequest,
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    board = await repo.create(
        user_id=user.user_id,
        title=body.title or _DEFAULT_TITLE,
    )
    return BoardSummary.model_validate(board)


@router.get("", response_model=list[BoardSummary])
async def list_boards(
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    boards = await repo.list_by_user(user.user_id)
    return [BoardSummary.model_validate(b) for b in boards]


@router.get("/{board_id}", response_model=BoardDetail)
async def get_board(
    board_id: str,
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    board = await repo.get_by_id(board_id, user_id=user.user_id)
    if not board:
        raise NotFoundError("白板不存在")
    return BoardDetail.model_validate(board)


@router.patch("/{board_id}", response_model=BoardSummary)
async def update_board(
    board_id: str,
    body: UpdateBoardRequest,
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    board = await repo.update_meta(
        board_id, user_id=user.user_id, title=body.title
    )
    if not board:
        raise NotFoundError("白板不存在")
    return BoardSummary.model_validate(board)


@router.put("/{board_id}/scene", response_model=BoardWriteResult)
async def write_board_scene(
    board_id: str,
    body: BoardSceneWriteRequest,
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    """CAS-write the scene (autosave). A stale ``baseline`` returns ``conflict=True``
    with the live board (never a blind overwrite); the client reconciles or forces the
    write with ``baseline=null``."""
    board, conflict = await repo.save_scene(
        board_id, user_id=user.user_id, scene=body.scene, baseline=body.baseline
    )
    if not board:
        raise NotFoundError("白板不存在")
    if conflict:
        return BoardWriteResult(
            ok=False,
            version=board.version,
            conflict=True,
            board=BoardDetail.model_validate(board),
        )
    return BoardWriteResult(ok=True, version=board.version)


@router.post("/{board_id}/conversation", response_model=BoardConversationResponse)
async def ensure_board_conversation(
    board_id: str,
    user: AuthUser,
    session: AsyncSession = Depends(get_db),
):
    """Get (or lazily mint) the board's dedicated AI conversation (AI协作白板.md §三 A / M2).

    Idempotent: returns the existing ``conversation_id`` if the board already has one;
    otherwise creates a bare chat (titled like the board, no folder) and binds it.
    Both repos share one session so the create + link commit together. The canvas calls
    this before its first AI turn, then runs the turn on the returned conversation.
    """
    boards = BoardRepository(session)
    board = await boards.get_by_id(board_id, user_id=user.user_id)
    if not board:
        raise NotFoundError("白板不存在")
    if board.conversation_id:
        return BoardConversationResponse(conversation_id=board.conversation_id)
    conversations = ConversationRepository(session)
    # Board AI sits on a bare chat (scratch). Leftover ``Board.folder_id`` is unread;
    # 「实现到工作区」picks a desk at that moment, not at board creation.
    conv = await conversations.create(
        user_id=user.user_id,
        title=board.title or _DEFAULT_TITLE,
        folder_id=None,
    )
    await boards.attach_conversation(
        board_id, user_id=user.user_id, conversation_id=conv.id
    )
    return BoardConversationResponse(conversation_id=conv.id)


@router.delete("/{board_id}", response_model=StatusResponse)
async def delete_board(
    board_id: str,
    user: AuthUser,
    repo: BoardRepository = Depends(get_board_repo),
):
    deleted = await repo.soft_delete(board_id, user_id=user.user_id)
    if not deleted:
        raise NotFoundError("白板不存在")
    return StatusResponse()
