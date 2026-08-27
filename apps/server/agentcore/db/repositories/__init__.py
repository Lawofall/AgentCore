"""Data access layer (Repository pattern), split by domain.

Each repository handles CRUD for a single model:
- Only data access, no business logic
- Uses select() builder pattern
- Pagination returns (data, total_count)
- Default sort: created_at desc

Transaction boundary (who commits)
----------------------------------
Canonical: **caller owns the unit-of-work**. Write methods default to
``commit=True`` (single-op CRUD / thin routers). Multi-step composites MUST
pass ``commit=False`` on each step and ``session.commit()`` once — see
``_base.commit_or_flush`` and [后端架构 §事务边界](/docs/02-架构/后端架构.md).
Do not mix a mid-composite ``commit=True`` with other writes on the same session.

This package was split out of a single ``repositories.py`` along domain seams
(file-splitting.mdc). This ``__init__`` re-exports the full public surface so the
historical import path — ``from agentcore.db.repositories import XRepository`` —
keeps working unchanged across the codebase. ``_ilike_pattern`` is re-exported
because the global-search tests import it directly.

``TurnLeaseRepository`` lives under ``agentcore.runtime.leases`` (swappable
backend seam) — import it from there, not from this package.
"""

from ._base import _UNSET, _ilike_pattern
from .admin_audit import AdminAuditRepository
from .admin_mfa import AdminMfaRepository
from .agent_audit import AgentAuditEventRepository
from .auth import (
    CredentialsRepository,
    RefreshTokenRepository,
    UserLlmProviderRepository,
)
from .billing import CostEventRepository
from .boards import BoardRepository
from .bookmarks import BookmarkRepository
from .browser import BrowserTakeoverRepository
from .chat import ChatRepository
from .conversation_shares import ConversationShareRepository
from .conversations import ConversationRepository
from .devices import PushDeviceRepository
from .documents import DocumentRepository
from .email_auth import EmailChallengeRepository, PendingRegistrationRepository
from .external_grants import ExternalGrantRepository
from .feedback import FeedbackRepository
from .folders import FolderRepository
from .friends import FriendRepository
from .llm_profiles import LlmModelProfileRepository
from .memory_pipeline import MemoryPipelineRepository
from .memory_updates import MemoryUpdateRepository
from .messages import MessageRepository
from .notices import ProductNoticeRepository
from .platform_credentials import PlatformCredentialRepository
from .runs import (
    HandoffJobRepository,
    PausedTurnRepository,
    RunSessionRepository,
    TurnJournalRepository,
    TurnMetricsRepository,
)
from .shared_spaces import SharedSpaceRepository
from .standing_tasks import StandingTaskRepository, StandingTaskRunRepository
from .stream_state import TurnStreamStateRepository
from .user_workflows import UserWorkflowRepository
from .users import (
    UserBlockRepository,
    UserDirectoryRepository,
    UserRepository,
)

__all__ = [
    "_UNSET",
    "_ilike_pattern",
    "AdminAuditRepository",
    "AgentAuditEventRepository",
    "AdminMfaRepository",
    "BookmarkRepository",
    "BoardRepository",
    "BrowserTakeoverRepository",
    "ChatRepository",
    "ConversationRepository",
    "ConversationShareRepository",
    "CostEventRepository",
    "DocumentRepository",
    "EmailChallengeRepository",
    "CredentialsRepository",
    "ExternalGrantRepository",
    "FeedbackRepository",
    "FolderRepository",
    "HandoffJobRepository",
    "LlmModelProfileRepository",
    "MemoryPipelineRepository",
    "MemoryUpdateRepository",
    "MessageRepository",
    "PausedTurnRepository",
    "PendingRegistrationRepository",
    "PlatformCredentialRepository",
    "ProductNoticeRepository",
    "PushDeviceRepository",
    "RefreshTokenRepository",
    "RunSessionRepository",
    "SharedSpaceRepository",
    "StandingTaskRepository",
    "StandingTaskRunRepository",
    "TurnJournalRepository",
    "TurnMetricsRepository",
    "TurnStreamStateRepository",
    "UserBlockRepository",
    "UserDirectoryRepository",
    "FriendRepository",
    "UserLlmProviderRepository",
    "UserRepository",
    "UserWorkflowRepository",
]
