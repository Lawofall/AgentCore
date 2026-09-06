"""Unit tests for the DB schema-error classifier (background-sweep log escalation).

A schema fault (undefined table/column = pending migration) must classify as
persistent so sweeps log it at ``error``; transient DB errors stay ``warning``.

Connectivity faults (connection refused / WinError 1225) map to the stable
``DatabaseUnavailableError`` message used by tools and sidecar honest-fail paths.
"""

from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.exc import TimeoutError as SATimeoutError

from agentcore.db.errors import (
    DATABASE_UNAVAILABLE_CODE,
    DATABASE_UNAVAILABLE_MESSAGE,
    DatabaseUnavailableError,
    SidecarLocalDbForbiddenError,
    is_db_connectivity_error,
    is_pool_timeout_error,
    is_schema_error,
    reraise_as_database_unavailable,
)


def _programming_error() -> ProgrammingError:
    # Mirrors what asyncpg raises for a missing table, wrapped by SQLAlchemy.
    orig = Exception('relation "run_sessions" does not exist')
    return ProgrammingError("SELECT 1", {}, orig)


def test_is_schema_error_true_for_programming_error():
    assert is_schema_error(_programming_error()) is True


def test_is_schema_error_false_for_operational_error():
    # A transient connectivity blip — should stay at warning, not escalate.
    err = OperationalError("SELECT 1", {}, Exception("connection reset"))
    assert is_schema_error(err) is False


def test_is_schema_error_false_for_plain_exception():
    assert is_schema_error(RuntimeError("boom")) is False


def test_is_db_connectivity_error_for_operational_refused():
    err = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))
    assert is_db_connectivity_error(err) is True


def test_is_db_connectivity_error_for_winerror_1225():
    err = OSError(1225, "远程计算机拒绝网络连接")
    # On non-Windows, constructor may set errno rather than winerror — still match
    # ConnectionRefused / errno path, or set winerror explicitly when available.
    if getattr(err, "winerror", None) is None:
        err.winerror = 1225  # type: ignore[attr-defined]
    assert is_db_connectivity_error(err) is True


def test_is_db_connectivity_error_false_for_schema():
    assert is_db_connectivity_error(_programming_error()) is False


def test_is_db_connectivity_error_false_for_plain():
    assert is_db_connectivity_error(RuntimeError("project not found")) is False


def test_reraise_as_database_unavailable_rewrites_connect_refuse():
    err = OperationalError("SELECT 1", {}, ConnectionRefusedError("connection refused"))
    try:
        reraise_as_database_unavailable(err)
        raise AssertionError("expected DatabaseUnavailableError")
    except DatabaseUnavailableError as wrapped:
        assert str(wrapped) == DATABASE_UNAVAILABLE_MESSAGE
        assert "1225" not in str(wrapped)
        assert wrapped.__cause__ is err


def test_reraise_as_database_unavailable_leaves_business_errors():
    err = RuntimeError("folder not found")
    try:
        reraise_as_database_unavailable(err)
    except DatabaseUnavailableError:
        raise AssertionError("must not rewrite business errors") from None
    # Function returns None when not connectivity — caller re-raises original.


def test_database_unavailable_code_stable():
    assert DATABASE_UNAVAILABLE_CODE == "database_unavailable"
    assert "服务暂时不可用" in DATABASE_UNAVAILABLE_MESSAGE
    assert "请确认数据库" not in DATABASE_UNAVAILABLE_MESSAGE


def test_pool_timeout_is_not_connectivity_but_reraises_product_error():
    err = SATimeoutError(
        "QueuePool limit of size 16 overflow 16 reached, connection timed out, timeout 30.00"
    )
    assert is_pool_timeout_error(err) is True
    assert is_db_connectivity_error(err) is False
    try:
        reraise_as_database_unavailable(err)
        raise AssertionError("expected DatabaseUnavailableError")
    except DatabaseUnavailableError as wrapped:
        assert str(wrapped) == DATABASE_UNAVAILABLE_MESSAGE
        assert wrapped.status_code == 503
        assert wrapped.__cause__ is err


def test_sidecar_local_db_forbidden_is_not_rewritten_as_unavailable():
    err = SidecarLocalDbForbiddenError("ticketed sidecar must not open local Postgres")
    assert is_db_connectivity_error(err) is False
    reraise_as_database_unavailable(err)
