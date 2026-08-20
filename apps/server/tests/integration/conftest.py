"""Integration-test fixtures backed by a real PostgreSQL instance.

Each test runs against a throwaway, per-process ``agentcore_it_<pid>`` schema that
is created and dropped per test, so integration tests never touch dev data, stay
isolated from one another, and — crucially — don't collide when several pytest
processes (parallel agents / pytest-xdist workers) hit the same server at once.
Using a dedicated *schema* (not a separate database) means no CREATEDB privilege
is required — the app role already owns its database.

When no PostgreSQL is reachable the posture depends on the environment: **locally**
tests skip (unit-only work stays unblocked), **on CI** they fail. See
``_integration_db_required``. The target server comes from ``TEST_DATABASE_URL``
(falls back to the app's ``DATABASE_URL``).
"""

import os
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from typing import NoReturn

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from sqlalchemy import text
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import agentcore.db.models  # noqa: F401  (register models on Base.metadata)
from agentcore.api.dependencies import get_db
from agentcore.config import settings
from agentcore.db.base import Base
from agentcore.db.base import engine as app_engine
from agentcore.db.base import probe_engine as app_probe_engine
from agentcore.db.base import telemetry_engine as app_telemetry_engine
from agentcore.db.repositories import (
    AdminMfaRepository,
    CredentialsRepository,
    UserRepository,
)
from agentcore.db.repositories.chat import OFFICIAL_CHAT_ID, OFFICIAL_CHAT_TITLE
from agentcore.main import app
from agentcore.middleware.csrf import CSRF_HEADER
from agentcore.security import hash_password
from agentcore.security.keys import KeyEncryptor

_TEST_MFA_SECRET = "JBSWY3DPEHPK3PXP"
_MASTER_KEY = "ab" * 32
TEST_PASSWORD = "password123"


def client_platform_headers(platform: str = "desktop") -> dict[str, str]:
    """Auth login/token require an explicit ``X-Client-Platform`` (fail-closed)."""
    return {"X-Client-Platform": platform}


async def register_and_login(
    client: httpx.AsyncClient,
    username: str,
    *,
    platform: str = "desktop",
    password: str = TEST_PASSWORD,
) -> str:
    """Register a product user and complete cookie login (product sessions have no MFA).

    Returns the signed-in user's id from ``LoginResponse.user``.
    """
    r = await client.post(
        "/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
        headers=client_platform_headers(platform),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert not body.get("mfa_required"), body
    user = body.get("user")
    assert user is not None, body
    return user["id"]


async def login_admin(client: httpx.AsyncClient, username: str, password: str) -> None:
    """Complete admin login (password + TOTP) on the admin client platform."""
    import pyotp

    admin_headers = client_platform_headers("admin")
    r = await client.post(
        "/v1/auth/login",
        json={"username": username, "password": password},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    if body.get("mfa_required"):
        code = pyotp.TOTP(_TEST_MFA_SECRET).now()
        r = await client.post(
            "/v1/auth/login/mfa",
            json={"pending_token": body["pending_token"], "code": code},
            headers=admin_headers,
        )
        assert r.status_code == 200, r.text


@pytest.fixture(autouse=True)
def _test_encryption_key(monkeypatch) -> Iterator[None]:
    """BYOK + admin MFA tests need a configured master key."""
    monkeypatch.setattr(settings, "encryption_key", _MASTER_KEY)
    yield


@pytest.fixture(autouse=True)
def _enforce_csrf(monkeypatch) -> None:
    """Pin CSRF **on** for the whole integration suite.

    It used to be pinned *off* for everything but ``@pytest.mark.csrf``, which meant
    ~450 cookie-session tests exercised a request path production never serves: no
    middleware ordering, exemption-prefix or token-refresh regression could fail
    here. The clients below echo the token the way apps/desktop and apps/admin do,
    so enforcement costs the suite nothing. Also isolates from a local ``.env`` that
    sets ``CSRF_ENABLED=false``.
    """
    monkeypatch.setattr(settings, "csrf_enabled", True)
    # Immediate-create hatch stays on so register_and_login / existing suites work.
    monkeypatch.setattr(settings, "legacy_register_enabled", True)
    monkeypatch.setattr(settings, "require_email_verified", False)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def csrf_echo_hooks() -> dict[str, list]:
    """httpx event hooks that make a test client behave like a compliant cookie-session
    client: remember whatever ``X-CSRF-Token`` the server hands out (login, refresh, or
    the 403 that re-arms) and echo it on mutating requests.

    A test that passes its own ``X-CSRF-Token`` wins (the hook never overwrites), so
    bad-token cases stay expressible; use ``naive_client`` for the no-token case.
    """
    held: dict[str, str] = {}

    async def _on_request(request: httpx.Request) -> None:
        if request.method in _SAFE_METHODS or CSRF_HEADER in request.headers:
            return
        token = held.get("token")
        if token:
            request.headers[CSRF_HEADER] = token

    async def _on_response(response: httpx.Response) -> None:
        token = response.headers.get(CSRF_HEADER)
        if token:
            held["token"] = token

    return {"request": [_on_request], "response": [_on_response]}


# Per-process schema so concurrent test runs never DROP/CREATE the *same* schema
# out from under each other. A shared name lets a second pytest process (parallel
# agent / pytest-xdist worker) wipe the first run's rows mid-test ("用户名或密码错误")
# or race its CREATE ("schema already exists"). PID is unique per live process and
# distinguishes xdist workers (each a separate process); the per-test
# DROP-before-CREATE below still self-heals a same-PID orphan from a crashed run.
_TEST_SCHEMA = f"agentcore_it_{os.getpid()}"


def _test_db_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or settings.database_url


_FALSEY = {"", "0", "false", "no", "off"}


def _integration_db_required() -> bool:
    """Is a reachable PostgreSQL mandatory here, or may the suite skip itself away?

    CI must have a database. Skipping there drops this whole directory — including
    contracts that live nowhere else (Stop API idempotence, cookie-session IDOR,
    repository cascades) — while the run still reports green, i.e. a dead gate.
    A dev box legitimately has no Postgres, so local runs keep skipping.

    ``REQUIRE_INTEGRATION_DB`` overrides the autodetect in both directions (``1`` to
    demand a database anywhere, ``0`` for a CI job that deliberately runs unit-only).
    """
    explicit = os.environ.get("REQUIRE_INTEGRATION_DB")
    if explicit is not None:
        return explicit.strip().lower() not in _FALSEY
    # ``CI`` is set by GitHub Actions and every other mainstream runner.
    return os.environ.get("CI", "").strip().lower() not in _FALSEY


def _no_postgres(exc: BaseException) -> NoReturn:
    reason = f"PostgreSQL not available for integration tests: {exc}"
    if _integration_db_required():
        pytest.fail(
            f"{reason}\n"
            f"  url: {_test_db_url()}\n"
            "  CI must provide PostgreSQL — skipping here would silently drop the entire "
            "integration suite while still reporting green. Start a database (see the "
            "ci.yml backend job's postgres service), or set REQUIRE_INTEGRATION_DB=0 to "
            "deliberately run unit-only.",
            pytrace=False,
        )
    pytest.skip(reason)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker]:
    # search_path = our schema only (plus implicit pg_catalog): keeps create_all
    # from seeing dev tables in `public` and skipping table creation.
    engine = create_async_engine(
        _test_db_url(),
        connect_args={"server_settings": {"search_path": _TEST_SCHEMA}},
        poolclass=NullPool,
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_TEST_SCHEMA} CASCADE"))
            await conn.execute(text(f"CREATE SCHEMA {_TEST_SCHEMA}"))
            await conn.run_sync(Base.metadata.create_all)
            # Mirror the official-chat migration seed (create_all does not run
            # alembic data inserts). Required so notice publish → IM works.
            await conn.execute(
                text(
                    """
                    INSERT INTO chats (id, type, title, auto_join, created_at, updated_at)
                    VALUES (CAST(:id AS uuid), 'official', :title, true, now(), now())
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": OFFICIAL_CHAT_ID, "title": OFFICIAL_CHAT_TITLE},
            )
    except (OperationalError, InterfaceError, OSError) as exc:
        await engine.dispose()
        _no_postgres(exc)

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _override_get_db() -> AsyncIterator:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    try:
        yield factory
    finally:
        app.dependency_overrides.pop(get_db, None)
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {_TEST_SCHEMA} CASCADE"))
        await engine.dispose()


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[httpx.AsyncClient]:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", event_hooks=csrf_echo_hooks()
    ) as c:
        yield c


@pytest_asyncio.fixture
async def naive_client(session_factory) -> AsyncIterator[httpx.AsyncClient]:
    """A client that does *not* echo the CSRF token — for asserting enforcement."""
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture
def new_client(session_factory) -> Callable:
    """Factory for additional clients with independent cookie jars (e.g. IDOR tests)."""

    @asynccontextmanager
    async def _make() -> AsyncIterator[httpx.AsyncClient]:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test", event_hooks=csrf_echo_hooks()
        ) as c:
            yield c

    return _make


@pytest_asyncio.fixture(autouse=True)
async def _dispose_app_engine_pool() -> AsyncIterator[None]:
    """Drain the process-global engines after each test.

    ``database_ready()`` (readiness probe + admin 系统/概览 panels) uses the global
    ``probe_engine`` (NullPool), *not* the per-test ``get_db`` override. With
    function-scoped event loops a connection can bind to the first test's loop, so
    the next probe hits a closed loop ("Event loop is closed"). Disposing here —
    inside each test's own loop — guarantees the next probe opens fresh.
    """
    yield
    await app_engine.dispose()
    await app_telemetry_engine.dispose()
    dispose = getattr(app_probe_engine, "dispose", None)
    if dispose is not None:
        result = dispose()
        if hasattr(result, "__await__"):
            await result


@pytest.fixture(autouse=True)
def _disable_rate_limit() -> Iterator[None]:
    """Rate-limit state is process-global; disable it so per-IP counters don't
    accumulate across the many auth POSTs the suite makes (429 is covered by the
    dedicated unit tests in test_rate_limit.py)."""
    original = settings.rate_limit_enabled
    settings.rate_limit_enabled = False
    yield
    settings.rate_limit_enabled = original


@pytest.fixture(autouse=True)
def _open_registration(monkeypatch) -> None:
    """Almost every integration test seeds throwaway users via ``register_and_login``;
    pin registration open so a local ``.env`` (``REGISTRATION_OPEN=false`` — a legitimate
    prod/local config) can't 403 them during setup. Tests that specifically exercise
    closed registration re-patch it to False in-body, which overrides this (same
    function-scoped monkeypatch, applied after fixture setup)."""
    monkeypatch.setattr(settings, "registration_open", True)


@pytest.fixture(autouse=True)
def _pin_billing_mode_byok(monkeypatch) -> None:
    """Pin billing_mode=byok so a local ``.env`` with ``BILLING_MODE=platform``
    cannot open the platform catalog for keyless-BYOK tests that expect 402."""
    monkeypatch.setattr(settings, "billing_mode", "byok")


@pytest.fixture(autouse=True)
def _stub_entry_description_schedule(monkeypatch) -> None:
    """Documents API schedules async description fill after empty saves.

    Integration suites must not hit a real LLM (20s timeout × many creates).
    Empty-only write semantics are covered by unit tests +
    ``test_apply_description_if_empty_column_only_preserves_content``.
    """
    monkeypatch.setattr(
        "agentcore.documents.description.schedule_description_generation",
        lambda **_kwargs: None,
    )


@pytest_asyncio.fixture
async def make_admin(session_factory) -> Callable:
    """Return an async helper that seeds an admin user (with credentials)."""

    async def _make(username: str = "admin", password: str = "adminpass123") -> tuple[str, str]:
        enc = KeyEncryptor(_MASTER_KEY)
        async with session_factory() as session:
            user = await UserRepository(session).create(
                username=username, display_name=username, role="admin"
            )
            await CredentialsRepository(session).create(
                user_id=user.user_id, password_hash=hash_password(password)
            )
            await AdminMfaRepository(session).upsert_pending(
                user_id=user.user_id,
                totp_secret_enc=enc.encrypt(_TEST_MFA_SECRET.encode()),
            )
            await AdminMfaRepository(session).enable(
                user.user_id,
                recovery_codes_hash=[],
            )
        return username, password

    return _make
