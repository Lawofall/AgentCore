"""Set a user's per-user quota overrides (成本配额与计费.md §一).

Operator tool for granting a specific account more (or unlimited) headroom during
内测, without touching the database by hand. The same ``UserRepository.set_quota``
this calls backs the admin 成员-page endpoint.

Run from ``apps/server``::

    # never cost-cap this user
    uv run python scripts/set_quota.py alice --unlimited

    # raise just the daily token budget, leave the rest inheriting global config
    uv run python scripts/set_quota.py bob --daily-tokens 5000000

    # cap monthly spend at ¥20
    uv run python scripts/set_quota.py carol --monthly-cny 20

Semantics per dimension: a number sets the override (``0`` = unlimited for that
dimension); ``inherit`` clears it back to the global config threshold; omitting the
flag leaves it unchanged. ``--unlimited / --no-unlimited`` toggles the master
bypass.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from agentcore.db import async_session_factory
from agentcore.db.repositories import UserRepository

# argparse default meaning "flag not given → leave this column unchanged",
# distinct from an explicit None (= clear the override / inherit global config).
_UNSET: object = object()


def _opt_int(raw: str) -> int | None:
    return None if raw.strip().lower() == "inherit" else int(raw)


def _opt_float(raw: str) -> float | None:
    return None if raw.strip().lower() == "inherit" else float(raw)


def _fmt(value: object) -> str:
    """Render an override column for the summary: None = inherit global config."""
    return "inherit" if value is None else str(value)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Set a user's per-user quota overrides.")
    p.add_argument("username", help="target account username")
    p.add_argument(
        "--unlimited",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="master bypass: skip all quota checks (--no-unlimited to clear)",
    )
    p.add_argument(
        "--daily-tokens",
        type=_opt_int,
        default=_UNSET,
        metavar="N|inherit",
        help="daily token cap (0 = unlimited; 'inherit' clears the override)",
    )
    p.add_argument(
        "--monthly-cny",
        type=_opt_float,
        default=_UNSET,
        metavar="X|inherit",
        help="monthly cost cap in CNY (0 = unlimited; 'inherit' clears)",
    )
    p.add_argument(
        "--daily-cny",
        type=_opt_float,
        default=_UNSET,
        metavar="X|inherit",
        help="daily cost cap in CNY (0 = unlimited; 'inherit' clears)",
    )
    p.add_argument(
        "--search-daily",
        type=_opt_int,
        default=_UNSET,
        metavar="N|inherit",
        help="platform search count per day (0 = unlimited; 'inherit' clears)",
    )
    p.add_argument(
        "--search-monthly",
        type=_opt_int,
        default=_UNSET,
        metavar="N|inherit",
        help="platform search count per month (0 = unlimited; 'inherit' clears)",
    )
    return p.parse_args()


async def _run(args: argparse.Namespace) -> None:
    async with async_session_factory() as session:
        users = UserRepository(session)
        user = await users.get_by_username(args.username)
        if user is None:
            print(f"user {args.username!r} not found", file=sys.stderr)
            raise SystemExit(1)

        kwargs: dict[str, object] = {}
        if args.unlimited is not None:
            kwargs["is_unlimited"] = args.unlimited
        if args.daily_tokens is not _UNSET:
            kwargs["daily_tokens"] = args.daily_tokens
        if args.monthly_cny is not _UNSET:
            kwargs["monthly_cost_cny"] = args.monthly_cny
        if args.daily_cny is not _UNSET:
            kwargs["daily_cost_cny"] = args.daily_cny
        if args.search_daily is not _UNSET:
            kwargs["search_daily"] = args.search_daily
        if args.search_monthly is not _UNSET:
            kwargs["search_monthly"] = args.search_monthly

        if not kwargs:
            print("nothing to change (pass --unlimited/--daily-tokens/...).\n")
        else:
            await users.set_quota(user.user_id, **kwargs)
            user = await users.get_by_id(user.user_id)
            print(f"updated quota for {args.username!r} (id={user.user_id})\n")

        print(f"  is_unlimited        : {user.is_unlimited}")
        print(f"  quota_daily_tokens  : {_fmt(user.quota_daily_tokens)}")
        print(f"  quota_monthly_cny   : {_fmt(user.quota_monthly_cost_cny)}")
        print(f"  quota_daily_cny     : {_fmt(user.quota_daily_cost_cny)}")
        print(f"  quota_search_daily  : {_fmt(user.quota_search_daily)}")
        print(f"  quota_search_monthly: {_fmt(user.quota_search_monthly)}")


if __name__ == "__main__":
    asyncio.run(_run(_parse_args()))
