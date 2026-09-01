"""Shared test fixtures + tmp-dir strategy.

The ``pytest_configure`` hook here is the single owner of pytest's temp-dir location
(``pyproject.toml`` deliberately sets NO ``--basetemp``). See the hook docstring for
the Windows WinError 5 traps it dodges.
"""

import contextlib
import os
import shutil
import stat
import tempfile
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

os.environ["LOG_LEVEL"] = "WARNING"

import pytest
import pytest_asyncio

# A stale per-session tmp dir is reaped only once it is older than this — long past any
# real test session, so a concurrently-running pytest (parallel agents / xdist, per the
# integration conftest) never has its LIVE basetemp deleted out from under it.
_TMP_PREFIX = "agentcore_pytest_"
_TMP_REAP_AGE_S = 6 * 3600


@pytest.fixture(autouse=True)
def _mark_test_traffic():
    """Tag all pytest log lines as synthetic (``traffic=test``); restore on teardown.

    Real user traffic never binds ``traffic`` — absence means production. Scoped via
    ``log_context`` so the key cannot leak into a later test's assertions.
    """
    from agentcore.core.log_context import log_context

    with log_context(traffic="test"):
        yield


@pytest.fixture(autouse=True)
def _isolate_prompt_profile():
    """No leaked eval prompt variant may rewrite base / CEO core for a later test."""
    from agentcore.runtime.resolve.profile import use_profile

    with use_profile(None):
        yield


@pytest.fixture(autouse=True)
def _isolate_coordination_registry():
    """Clear the module-global coordination session registry around every test.

    Delegate tests share ``execution_id="e"`` — a leaked active session makes later
    delegates silently MERGE into the stale team (「队员已追加」) instead of starting
    fresh. Lives in the ROOT conftest (not tests/delegate/conftest.py) deliberately:
    a directory-level autouse fixture silently drops when that directory's files are
    passed on the CLI non-contiguously (delegate file → tests-root file → delegate
    file — pytest collects the directory as two Package nodes and the second loses
    the directory conftest's autouse binding). Root autouse survives any order.
    """
    from agentcore.runtime.coordination.session import clear_active_coordination

    clear_active_coordination()
    yield
    clear_active_coordination()


@pytest_asyncio.fixture(autouse=True)
async def _isolate_index_registry() -> AsyncIterator[None]:
    """Abort leftover ``code-index-maintain`` tasks before the test loop closes.

    ``ServerWorkspace`` mutations fire-and-forget ``IndexMaintainer.schedule``.
    Under xdist load that task is often still inside ``asyncio.to_thread`` when
    pytest-asyncio shuts the function-scoped loop's default executor —
    ``RuntimeError: Executor shutdown has been called`` on
    ``BM25Index.list_indexed_paths``. Drain while the loop is still alive.
    """
    from agentcore.workspace.indexing.registry import drain_index_registry

    await drain_index_registry()
    yield
    await drain_index_registry()


@pytest.fixture(autouse=True)
def _isolate_llm_cooldown_gate():
    """Drop the process-wide 429 cooldown so a leaked day-reset cannot starve later tests.

    Provider tests reuse api_key ``k``; without a reset the next case would wait
    at the gate (or refuse) instead of hitting the mock transport.
    """
    from agentcore.llm.provider.cooldown_gate import reset_cooldown_gate

    reset_cooldown_gate()
    yield
    reset_cooldown_gate()


@pytest.fixture(autouse=True)
def _isolate_platform_credential_pool():
    """Empty the in-memory platform-pool snapshot around every test.

    ``platform_llm_credentials`` reads this snapshot synchronously; a leaked
    enabled member would silently replace the env key the rest of the suite
    expects.
    """
    from agentcore.llm.platform_pool import replace_platform_pool_snapshot

    replace_platform_pool_snapshot(())
    yield
    replace_platform_pool_snapshot(())


@pytest.fixture(autouse=True)
def _isolate_platform_pool_state():
    """Drop fill-first / 429 cooling / sticky pins so suite order cannot leak."""
    from agentcore.llm.platform_pool_state import reset_pool_state_store

    reset_pool_state_store()
    yield
    reset_pool_state_store()


@pytest.fixture(autouse=True)
def _isolate_turn_scoped_closing_state():
    """Give every test the turn-entry reset that prepare / resume wire perform.

    Storm latches and ``current_delivery_verdict`` are ContextVars set as side effects
    of delivery_status emission. Tests that drive the engine loop directly skip
    ``prepare``, so without this they inherit the previous test's latches and
    ``finish_guard`` injects spurious 缺口承认影子（或旧超席回炉）depending on
    collection order. Calls the same owner as production so the two cannot drift.
    """
    from agentcore.runtime.closing_posture import reset_turn_scoped_closing_state

    reset_turn_scoped_closing_state()
    yield
    reset_turn_scoped_closing_state()


@pytest.fixture(autouse=True)
def _pin_cloud_execution_posture_to_defaults(monkeypatch):
    """Pin cloud execution off for every test (deterministic withheld posture).

    Production/内测 default is ``gvisor_enabled=true``; the suite still forces false so
    Windows/dev without runsc stay deterministic. Tests that exercise the enabled chain
    opt in via ``monkeypatch.setattr(settings, ...)``. Machine-local ``.env`` may also
    flip escape-hatch flags — same isolation idiom as ``_disarm_demo_tape_recorder``.
    """
    from agentcore.config import settings

    monkeypatch.setattr(settings, "code_execute_cloud_enabled", False)
    monkeypatch.setattr(settings, "code_execute_cloud_unsafe_ack", False)
    monkeypatch.setattr(settings, "gvisor_enabled", False)
    yield


@pytest.fixture(autouse=True)
def _pin_smtp_unconfigured(monkeypatch):
    """A local ``.env`` with real SMTP must not send mail from the unit suite."""
    from agentcore.config import settings

    monkeypatch.setattr(settings, "smtp_host", "")
    monkeypatch.setattr(settings, "smtp_from_address", "")
    yield


@pytest.fixture(autouse=True)
def _pin_legal_vertical_gate_to_defaults(monkeypatch):
    """Pin ``legal_vertical_enabled`` to its production default (off) for every test.

    Local ``apps/server/.env`` may turn the legal pack on for war-room probing; the
    suite must assert the default empty ``packs[]`` / no legal skills posture unless a
    test opts in via ``monkeypatch.setattr(settings, "legal_vertical_enabled", True)``.
    """
    from agentcore.config import settings

    monkeypatch.setattr(settings, "legal_vertical_enabled", False)
    yield


@pytest.fixture(autouse=True)
def _reset_cloud_sandbox_health():
    """Clear the boot-probe cache so a failed/ok injection cannot leak across tests."""
    from agentcore.tools.sandbox.cloud_health import reset_cloud_sandbox_health_for_tests

    reset_cloud_sandbox_health_for_tests()
    yield
    reset_cloud_sandbox_health_for_tests()


@pytest.fixture(autouse=True)
def _reset_sandboxd_client():
    from agentcore.tools.sandbox.sandboxd.client import reset_sandboxd_client_for_tests

    reset_sandboxd_client_for_tests()
    yield
    reset_sandboxd_client_for_tests()


@pytest.fixture(autouse=True)
def _reset_desk_sessions():
    from agentcore.tools.sandbox.gvisor import reset_desk_sessions_for_tests

    reset_desk_sessions_for_tests()
    yield
    reset_desk_sessions_for_tests()


@pytest.fixture(autouse=True)
def _capture_client_tool_deliveries(monkeypatch, request):
    """Make every CLIENT_TOOL fulfill delivery report DELIVERED and record the frame.

    Channel tests (workspace / host / mcp / board / terminal / …) drive a round trip by
    reading the delivered frame's ``request_id`` and settling the interaction registry,
    so they need ``deliver_client_tool`` to succeed without a real device attached.
    Owned here rather than copied per file: the capture buffer is a module global, and a
    single autouse owner is what guarantees it is cleared before every test. Read it via
    ``tests.client_tool_fulfill_testutil.await_captured_event``.

    A test that asserts the REAL dispatch outcome either wins on its own (a module- or
    test-level patch applies after this one; a directly imported function object is
    unreachable by ``setattr``) or opts out with ``@pytest.mark.real_fulfill_dispatch``.
    Opting out matters for the NO_FULFILLER path specifically: under this default the
    channel believes the frame landed and waits out its full timeout instead.
    """
    if "real_fulfill_dispatch" in request.keywords:
        yield
        return
    from tests.client_tool_fulfill_testutil import install_deliver_capture

    install_deliver_capture(monkeypatch)
    yield


@pytest.fixture(autouse=True)
def _disarm_demo_tape_recorder():
    """Clear the process-wide EventSink emit tap after every test.

    Sidecar ``initialize`` arms the recorder when ``DEMO_TAPE_RECORD_ENABLED`` is
    set (including via ``apps/server/.env``); without teardown the tap leaks into
    later demo_tape / pipeline tests and can hang the session.
    """
    yield
    from agentcore.demo_tape.recorder import uninstall_recorder

    uninstall_recorder()


@pytest.fixture(autouse=True)
def _reset_conversation_store():
    """Restore CloudStore after sidecar tests swap in a local OutboxStore.

    A leaked OutboxStore makes later EventSink checkpointers keep dirty forever
    under a no-op pacing wait (flush never settles). Demo-tape tests must patch
    ``demo_tape.player.pacing_sleep``, not process-wide ``asyncio.sleep``.
    """
    yield
    from agentcore.conversation.store import reset_conversation_store_for_tests
    from agentcore.sidecar.server_pkg.core import reset_active_sidecar_for_tests

    reset_conversation_store_for_tests()
    reset_active_sidecar_for_tests()


@pytest_asyncio.fixture(autouse=True)
async def _dispose_app_engine_pool() -> AsyncIterator[None]:
    """Drain the process-global SQLAlchemy pools after every test.

    Any unit test that opens a session binds pooled connections to that test's
    function-scoped event loop. The next test's StreamCheckpointer can then hit
    a dead connection (``'NoneType' object has no attribute 'send'``); with dirty
    channels never clearing, flush failures spam until timeout. Same idiom as
    ``tests/integration/conftest.py``.
    """
    yield
    from agentcore.db.base import engine as app_engine
    from agentcore.db.base import probe_engine as app_probe_engine
    from agentcore.db.base import telemetry_engine as app_telemetry_engine

    await app_engine.dispose()
    await app_telemetry_engine.dispose()
    dispose = getattr(app_probe_engine, "dispose", None)
    if dispose is not None:
        result = dispose()
        if hasattr(result, "__await__"):
            await result


def _rmtree_quiet(path: Path) -> None:
    """Recursively delete ``path``; NEVER raise.

    Clears the read-only bit first (git objects inside a workspace fixture are read-only
    on Windows, so rmtree's default handler raises WinError 5 on them) and swallows
    anything else — a dir a leaked subprocess still holds is in Windows "delete-pending"
    limbo and cannot be removed until that handle closes, so we skip it and let a later
    session reap it once the holder dies.
    """

    def _retry(func, target, _exc):  # noqa: ANN001 - shutil callback shape
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass

    with contextlib.suppress(OSError):
        shutil.rmtree(path, onexc=_retry)


def pytest_configure(config: pytest.Config) -> None:
    """Route pytest's tmp dirs to a unique-per-session folder under the OS temp dir.

    Two Windows WinError 5 traps motivate this — both turned a fully-passing run into a
    non-zero exit spamming ``PermissionError`` during cleanup:

    * The DEFAULT auto-numbered base (``<tmp>/pytest-of-<user>/``) keeps a
      ``pytest-current`` symlink whose cleanup throws (symlink stat denied).
    * A FIXED shared ``--basetemp`` (the previous workaround) is ``rm_rf``-reset at
      session START, *unsuppressed* — so the moment a prior run leaks a file handle (a
      ``SubprocessSandbox`` child whose CWD is the tmp workspace puts that dir into
      "delete-pending"), the next run's reset raises on every undeletable entry.

    A unique basetemp per session never needs a pre-run reset (a fresh path has nothing
    to delete), and pytest does not auto-clean an *explicit* basetemp at session end —
    so no ``rm_rf`` ever runs against a directory another live process might be holding.
    Leftovers land in TEMP (OS-reclaimed), never the repo. An explicit CLI
    ``--basetemp`` still wins (the guard below).
    """
    if config.option.basetemp:
        return
    root = Path(tempfile.gettempdir())
    # Self-maintaining: reap our OWN stragglers from past runs, but only ones old enough
    # that no concurrent session could still be using them (suppressed end-to-end).
    now = time.time()
    for stale in root.glob(f"{_TMP_PREFIX}*"):
        try:
            if now - stale.stat().st_mtime < _TMP_REAP_AGE_S:
                continue
        except OSError:
            continue
        _rmtree_quiet(stale)
    config.option.basetemp = str(root / f"{_TMP_PREFIX}{os.getpid()}_{uuid.uuid4().hex[:8]}")


@pytest.fixture
def anyio_backend():
    return "asyncio"


class LogSpy:
    """A drop-in replacement for a module's structlog ``logger`` that records every
    ``logger.info`` / ``.warning`` / ``.error`` / ``.debug`` call as ``(event, kwargs)``.

    Use via ``monkeypatch.setattr(some_module, "logger", LogSpy())`` to assert on a
    structured log line's FIELDS deterministically. This is the reliable alternative to
    ``structlog.testing.capture_logs`` here: ``cache_logger_on_first_use=True`` (core/
    logging.py) caches a module logger's bound methods on first use, so once an earlier
    test has exercised a module's logger, ``capture_logs`` no longer intercepts it. Swapping
    the module attribute sidesteps that entirely (config- and order-independent). Same idiom
    as ``test_source_domains._LogSpy``, hoisted here for the decision-observability tests.
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _record(self, event: str, *args: object, **kwargs: object) -> None:
        self.events.append((event, dict(kwargs)))

    info = _record
    warning = _record
    error = _record
    debug = _record

    def get(self, event: str) -> dict:
        """Return the kwargs of the one logged ``event`` (asserts exactly one was logged)."""
        matches = [kw for name, kw in self.events if name == event]
        assert len(matches) == 1, (
            f"expected exactly one {event!r} log, got {len(matches)} "
            f"(events: {[n for n, _ in self.events]})"
        )
        return matches[0]
