"""User / account data access: profile, roster, blocks, directory privacy."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from agentcore.core.types import new_id
from agentcore.db.models import CostEvent, RefreshToken, User, UserBlock, UserDirectorySettings

from ._base import _UNSET, _ilike_pattern, _sum_int, commit_or_flush


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, user_id: str) -> User | None:
        result = await self._session.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        lowered = username.strip().lower()
        result = await self._session.execute(
            select(User).where(func.lower(User.username) == lowered)
        )
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> User | None:
        """Find an account by exact email (case-insensitive) — the uniqueness
        pre-check for profile edits (个人资料编辑). ``email`` is a unique column, so a
        non-None match identifies the sole holder; the service rejects a change that
        would collide with another user.
        """
        result = await self._session.execute(
            select(User).where(func.lower(User.email) == email.strip().lower())
        )
        return result.scalar_one_or_none()

    async def get_by_ids(self, user_ids: Sequence[str]) -> dict[str, User]:
        """Fetch users by id, keyed by id — batch lookup for the chat list (avoids
        an N+1 when resolving dm peers / message senders).
        """
        if not user_ids:
            return {}
        result = await self._session.execute(select(User).where(User.user_id.in_(user_ids)))
        return {u.user_id: u for u in result.scalars().all()}

    async def search(self, query: str, *, limit: int = 20) -> Sequence[User]:
        """People-search for the 消息 page (任意搜人).

        Exact match only — case-insensitive username or exact ``user_id`` — no fuzzy
        prefix, so the directory cannot be enumerated by scanning. Disabled
        (``status != active``) accounts are excluded; discoverability
        (``user_directory_settings``) is enforced one layer up in the service.
        """
        q = query.strip()
        if not q:
            return []
        try:
            UUID(q)
            by_id = await self.get_by_id(q)
        except ValueError:
            by_id = None
        if by_id is not None and by_id.status == "active":
            return [by_id]
        result = await self._session.execute(
            select(User)
            .where(func.lower(User.username) == q.lower(), User.status == "active")
            .limit(limit)
        )
        return result.scalars().all()

    async def list_all(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        query: str | None = None,
        role: str | None = None,
        status: str | None = None,
        ip: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort: str = "created_at",
        order: str = "desc",
        include_deleted: bool = False,
    ) -> tuple[list[tuple[User, int]], int]:
        """All accounts for the admin console (用户管理), paginated, with each account's
        all-time cumulative spend (for the 累计成本 column + cost sort).

        Unlike ``search`` (exact-match, anti-enumeration for the 找人 directory), this
        is the operator's full roster. Filters (AND-combined): ``query`` substring
        ILIKEs username/display_name, ``role``/``status`` pin those dimensions,
        ``ip`` matches ``registration_ip`` or any ``refresh_tokens.ip`` (any status —
        historical login IPs count for 加强可查), ``since``/``until`` bound
        ``created_at``. 注销 (soft-deleted, anonymized → ``deleted_<id>``) accounts
        are excluded unless ``include_deleted`` — tombstones, noise for the live
        roster but kept for audit. ``sort`` ∈ {``created_at``, ``cost``} with
        ``order`` ∈ {``asc``, ``desc``} (cost ties break by newest-first for stable
        pagination). The total reflects the same filters. Returns
        ``([(user, cost_total_nano)], total)``.

        Cost is a LEFT JOIN onto a per-user SUM of ``cost_events.cost_total_nano`` (a
        never-spent account reads 0). MVP aggregates the ledger per roster page — fine
        at single-deploy scale (``cost_events`` is indexed on ``user_id``); a cached
        rollup is the escape hatch if the ledger outgrows it.
        """
        conditions: list[ColumnElement[bool]] = []
        if not include_deleted:
            conditions.append(User.deleted_at.is_(None))
        q = (query or "").strip()
        if q:
            pattern = _ilike_pattern(q)
            conditions.append(or_(User.username.ilike(pattern), User.display_name.ilike(pattern)))
        if role is not None:
            conditions.append(User.role == role)
        if status is not None:
            conditions.append(User.status == status)
        ip_q = (ip or "").strip()
        if ip_q:
            token_ip_match = (
                select(RefreshToken.id)
                .where(RefreshToken.user_id == User.user_id, RefreshToken.ip == ip_q)
                .exists()
            )
            conditions.append(or_(User.registration_ip == ip_q, token_ip_match))
        if since is not None:
            conditions.append(User.created_at >= since)
        if until is not None:
            conditions.append(User.created_at <= until)

        count_stmt = select(func.count()).select_from(User)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = await self._session.scalar(count_stmt)

        # Per-user lifetime spend; LEFT-joined so an account with no ledger row is 0.
        cost_subq = (
            select(
                CostEvent.user_id.label("uid"),
                _sum_int(CostEvent.cost_total_nano).label("cost_total"),
            )
            .group_by(CostEvent.user_id)
            .subquery()
        )
        cost_col = func.coalesce(cost_subq.c.cost_total, 0)

        list_stmt = select(User, cost_col.label("cost_total")).outerjoin(
            cost_subq, cost_subq.c.uid == User.user_id
        )
        if conditions:
            list_stmt = list_stmt.where(*conditions)

        if sort == "cost":
            primary = cost_col.asc() if order == "asc" else cost_col.desc()
            # Stable tiebreak: the many zero-spend accounts fall back to newest-first.
            list_stmt = list_stmt.order_by(primary, User.created_at.desc())
        else:
            list_stmt = list_stmt.order_by(
                User.created_at.asc() if order == "asc" else User.created_at.desc()
            )

        rows = (await self._session.execute(list_stmt.limit(limit).offset(offset))).all()
        return [(row[0], int(row[1])) for row in rows], int(total or 0)

    async def count_overview(self) -> dict[str, int]:
        """Account tallies for the admin system panel (管理员后台 P2 系统状态).

        One round-trip via conditional aggregation over the *live* population only —
        注销 (soft-deleted) accounts are anonymized tombstones, not part of the
        roster, so every tally excludes them: ``total`` counts live accounts
        (disabled included), ``active`` = live ``status == "active"``, ``admins`` =
        live ``role == "admin"``. Read-only — a deployment-health glance.
        """
        live = User.deleted_at.is_(None)
        stmt = select(
            func.count().filter(live).label("total"),
            func.count().filter(live, User.status == "active").label("active"),
            func.count().filter(live, User.role == "admin").label("admins"),
        ).select_from(User)
        row = (await self._session.execute(stmt)).one()
        return {
            "total": int(row.total),
            "active": int(row.active),
            "admins": int(row.admins),
        }

    async def create(
        self,
        *,
        username: str,
        display_name: str | None = None,
        email: str | None = None,
        email_verified_at: datetime | None = None,
        role: str = "user",
        status: str = "active",
        registration_ip: str | None = None,
        commit: bool = True,
    ) -> User:
        user = User(
            user_id=new_id(),
            username=username,
            display_name=display_name or "",
            email=email,
            email_verified_at=email_verified_at,
            role=role,
            status=status,
            registration_ip=registration_ip,
        )
        self._session.add(user)
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(user)
        return user

    async def set_role(self, user_id: str, role: str) -> None:
        await self._session.execute(update(User).where(User.user_id == user_id).values(role=role))
        await self._session.commit()

    async def set_status(self, user_id: str, status: str) -> None:
        """Enable/disable an account (admin 用户管理). A disabled user is refused at
        ``get_current_user`` on the next request, so no token revocation is needed.
        """
        await self._session.execute(
            update(User).where(User.user_id == user_id).values(status=status)
        )
        await self._session.commit()

    async def set_memory_enabled(self, user_id: str, enabled: bool) -> None:
        """Legacy writer for ``users.memory_enabled`` (column retained; 定案 A).

        Product resolve + user API no longer flip this gate. Kept for tests /
        one-off ops; do not call from user-facing routes.
        """
        await self._session.execute(
            update(User).where(User.user_id == user_id).values(memory_enabled=enabled)
        )
        await self._session.commit()

    async def set_conversation_history_access(self, user_id: str, enabled: bool) -> None:
        """Legacy writer for ``users.conversation_history_access`` (column retained; 定案 A).

        Product resolve + user API no longer flip this gate. Kept for tests /
        one-off ops; do not call from user-facing routes.
        """
        await self._session.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(conversation_history_access=enabled)
        )
        await self._session.commit()

    async def set_autonomy_policy(self, user_id: str, policy: str) -> None:
        """Set the user's capability-authorization posture (安全权限与治理 §三)."""
        await self._session.execute(
            update(User).where(User.user_id == user_id).values(autonomy_policy=policy)
        )
        await self._session.commit()

    async def set_default_model_profile(
        self, user_id: str, profile_id: str | None
    ) -> None:
        """Set the account default model combination (模型组合)."""
        await self._session.execute(
            update(User)
            .where(User.user_id == user_id)
            .values(default_model_profile_id=profile_id)
        )
        await self._session.commit()

    async def list_memory_enabled_user_ids(self) -> Sequence[str]:
        """All user ids for memory backfill scans (name kept; gate is product-always-on)."""
        result = await self._session.execute(select(User.user_id))
        return [row[0] for row in result.all()]

    async def set_quota(
        self,
        user_id: str,
        *,
        is_unlimited: bool | object = _UNSET,
        daily_tokens: int | None | object = _UNSET,
        monthly_cost_cny: float | None | object = _UNSET,
        daily_cost_cny: float | None | object = _UNSET,
        daily_requests: int | None | object = _UNSET,
    ) -> None:
        """Patch a user's per-user quota overrides (成本配额与计费.md §一).

        Only the fields actually passed are written, so callers can flip one knob
        without disturbing the others. For the override dimensions an explicit
        ``None`` clears the override back to「inherit global config」, while ``0``
        means「unlimited for this user」(distinct from ``_UNSET`` = leave unchanged).
        """
        values: dict[str, object] = {}
        if is_unlimited is not _UNSET:
            values["is_unlimited"] = is_unlimited
        if daily_tokens is not _UNSET:
            values["quota_daily_tokens"] = daily_tokens
        if monthly_cost_cny is not _UNSET:
            values["quota_monthly_cost_cny"] = monthly_cost_cny
        if daily_cost_cny is not _UNSET:
            values["quota_daily_cost_cny"] = daily_cost_cny
        if daily_requests is not _UNSET:
            values["quota_daily_requests"] = daily_requests
        if not values:
            return
        await self._session.execute(update(User).where(User.user_id == user_id).values(**values))
        await self._session.commit()

    async def update(
        self,
        user_id: str,
        *,
        display_name: str | object = _UNSET,
        email: str | None | object = _UNSET,
        email_verified_at: datetime | None | object = _UNSET,
        username: str | object = _UNSET,
        username_changed_at: datetime | None | object = _UNSET,
        commit: bool = True,
    ) -> User | None:
        """Patch a user's profile fields (个人资料编辑), returning the updated row.

        Only the fields actually passed are written (``_UNSET`` = leave unchanged), so
        the caller can change display name without touching email. An explicit ``None``
        email clears it. Uniqueness (email) is validated one layer up in the service.
        Returns ``None`` for an unknown id.
        """
        values: dict[str, object | None] = {}
        if display_name is not _UNSET:
            values["display_name"] = display_name
        if email is not _UNSET:
            values["email"] = email
        if email_verified_at is not _UNSET:
            values["email_verified_at"] = email_verified_at
        if username is not _UNSET:
            values["username"] = username
        if username_changed_at is not _UNSET:
            values["username_changed_at"] = username_changed_at
        if values:
            await self._session.execute(
                update(User).where(User.user_id == user_id).values(**values)
            )
            await commit_or_flush(self._session, commit=commit)
        return await self.get_by_id(user_id)

    async def set_avatar(self, user_id: str, avatar_key: str | None) -> User | None:
        """Set or clear the user's avatar storage key (头像), returning the row.

        ``None`` clears it (removed avatar / account anonymization). The object bytes
        themselves are managed by the caller (asset storage lives outside this layer).
        Returns ``None`` for an unknown id.
        """
        await self._session.execute(
            update(User).where(User.user_id == user_id).values(avatar_key=avatar_key)
        )
        await self._session.commit()
        return await self.get_by_id(user_id)

    async def soft_delete(self, user_id: str, *, commit: bool = True) -> User | None:
        """Soft-delete + anonymize an account (注销账户), returning the updated row.

        Stamps ``deleted_at``, disables the account (so ``get_current_user`` refuses it
        on the next request — live tokens die), and frees the unique identifiers for
        re-registration by anonymizing ``username`` → ``deleted_<id>``, clearing
        ``email`` and dropping the avatar key (the object is purged by the caller). The
        append-only cost ledger (不变量①) is intentionally untouched. Returns ``None``
        for an unknown id.

        Pass ``commit=False`` when pairing with refresh-token revoke in one txn.
        """
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.deleted_at = datetime.now(UTC)
        user.status = "disabled"
        user.username = f"deleted_{user_id}"
        user.email = None
        user.avatar_key = None
        await commit_or_flush(self._session, commit=commit)
        await self._session.refresh(user)
        return user


class UserBlockRepository:
    """Block list for the 消息 page (任意搜人 护栏): symmetric DM denial + report."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def is_blocked_between(self, user_a: str, user_b: str) -> bool:
        """True if either user blocked the other (blocks gate DMs both ways)."""
        result = await self._session.execute(
            select(func.count())
            .select_from(UserBlock)
            .where(
                or_(
                    and_(
                        UserBlock.user_id == user_a,
                        UserBlock.blocked_user_id == user_b,
                    ),
                    and_(
                        UserBlock.user_id == user_b,
                        UserBlock.blocked_user_id == user_a,
                    ),
                )
            )
        )
        return result.scalar_one() > 0

    async def block(self, user_id: str, blocked_user_id: str) -> None:
        stmt = (
            pg_insert(UserBlock)
            .values(user_id=user_id, blocked_user_id=blocked_user_id)
            .on_conflict_do_nothing(index_elements=["user_id", "blocked_user_id"])
        )
        await self._session.execute(stmt)
        await self._session.commit()

    async def unblock(self, user_id: str, blocked_user_id: str) -> None:
        await self._session.execute(
            delete(UserBlock).where(
                UserBlock.user_id == user_id,
                UserBlock.blocked_user_id == blocked_user_id,
            )
        )
        await self._session.commit()

    async def list_blocked(self, user_id: str) -> Sequence[str]:
        result = await self._session.execute(
            select(UserBlock.blocked_user_id).where(UserBlock.user_id == user_id)
        )
        return [row[0] for row in result.all()]


class UserDirectoryRepository:
    """Per-user discoverability + who-can-DM privacy (任意搜人 护栏).

    A missing row means defaults (discoverable, anyone can DM) — open search is
    the product default; users opt out by writing a row.
    """

    def __init__(self, session: AsyncSession):
        self._session = session

    async def get(self, user_id: str) -> UserDirectorySettings | None:
        result = await self._session.execute(
            select(UserDirectorySettings).where(UserDirectorySettings.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        user_id: str,
        *,
        discoverable: bool | object = _UNSET,
        who_can_dm: str | object = _UNSET,
        who_can_friend: str | object = _UNSET,
    ) -> UserDirectorySettings:
        settings = await self.get(user_id)
        if settings is None:
            settings = UserDirectorySettings(user_id=user_id)
            self._session.add(settings)
        if discoverable is not _UNSET:
            settings.discoverable = discoverable  # type: ignore[assignment]
        if who_can_dm is not _UNSET:
            settings.who_can_dm = who_can_dm  # type: ignore[assignment]
        if who_can_friend is not _UNSET:
            settings.who_can_friend = who_can_friend  # type: ignore[assignment]
        await self._session.commit()
        await self._session.refresh(settings)
        return settings
