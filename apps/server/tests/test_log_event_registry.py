"""Unit tests for the product-AI log event registry."""

from __future__ import annotations

import warnings

import pytest

from agentcore.observability.events import (
    EventSpec,
    FieldType,
    MapEventRegistry,
    UnregisteredLogEventError,
    UnregisteredLogEventWarning,
    check_event_registered,
    get_registry,
    registry_processor,
    reset_registry_for_tests,
)


@pytest.fixture(autouse=True)
def _restore_registry():
    yield
    reset_registry_for_tests(None)


def test_catalog_covers_key_runtime_events():
    names = get_registry().names()
    for required in (
        "chat.turn_start",
        "chat.turn_complete",
        "delegate.started",
        "tool.execute_end",
        "llm.call",
        "engine.loop_nudge",
        "cost.recorded",
        "pipeline.error",
        "http.unhandled_error",
        "approval.sandbox_auto_pass",
        "firehose.backpressure_drop",
        "event_sink.backpressure_drop",
    ):
        assert required in names, required
    assert "engine.finish_guard_auto_deep_read" not in names
    assert len(names) >= 100


def test_backpressure_drop_fields_registered():
    for name in ("event_sink.backpressure_drop", "firehose.backpressure_drop"):
        fields = get_registry().requires(name).fields
        assert "dropped_delta" in fields
        assert "dropped_total" in fields


def test_idle_eviction_logs_victim_id_not_canonical_conversation_id():
    # Victim cid must stay off canonical conversation_id so merge_contextvars
    # keeps the evicting request's user_id / trace_id.
    for name in (
        "roster.conversation_evicted",
        "search_cache.conversation_evicted",
        "url_cache.conversation_evicted",
    ):
        fields = get_registry().requires(name).fields
        assert "evicted_conversation_id" in fields
        assert "conversation_id" not in fields


def test_stream_detach_timing_fields_registered():
    detach = get_registry().requires("event_sink.detach").fields
    assert "duration_ms" in detach
    assert "idle_ms" in detach
    assert "started_at" in detach
    assert "mode" in detach
    assert "http_req_id" in detach
    attach = get_registry().requires("event_sink.attach").fields
    assert attach["mode"].name == "str"
    assert "message_id" in attach
    assert "http_req_id" in attach
    unwatch = get_registry().requires("conversation_stream.unwatch").fields
    assert "duration_ms" in unwatch
    assert "idle_ms" in unwatch
    assert "http.readyz_failed" in get_registry().names()
    assert "event_loop.lag" in get_registry().names()
    assert "disk.high_watermark" in get_registry().names()
    assert "disk.high_watermark" in get_registry().names()


def test_catalog_registers_failure_and_build_provenance_fields():
    """两个定性字段登记在册：包装层归因 + 线上版本归属（勿手改 catalog，跑同步脚本）。"""
    reg = get_registry()
    assert "error_type" in reg.requires("memory.consolidation_failed").fields
    started = reg.requires("server.started").fields
    assert "version" in started
    assert "git_sha" in started
    turn_start = reg.requires("chat.turn_start").fields
    assert "stream_path_reason" in turn_start


def test_execute_end_registers_shell_observe_fields():
    """terminal/host_shell 观测字段登记在册（command_preview 截断，不分类写盘）。"""
    fields = get_registry().requires("tool.execute_end").fields
    assert fields["command_preview"].name == "str"
    assert fields["cwd_preview"].name == "str"
    assert fields["subcommand"].name == "str"


def test_event_spec_rejects_bare_name():
    with pytest.raises(ValueError, match="component.action"):
        EventSpec(name="bare")


def test_check_unregistered_prod_loose():
    reg = MapEventRegistry([EventSpec(name="chat.turn_start")])
    # production: silent
    check_event_registered("totally.unknown", debug=False, registry=reg)


def test_check_unregistered_dev_warns():
    reg = MapEventRegistry([EventSpec(name="chat.turn_start")])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        check_event_registered("totally.unknown", debug=True, registry=reg)
    assert any(issubclass(w.category, UnregisteredLogEventWarning) for w in caught)


def test_check_unregistered_strict_raises(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("LOG_EVENT_REGISTRY_STRICT", "1")
    reg = MapEventRegistry([EventSpec(name="chat.turn_start")])
    with pytest.raises(UnregisteredLogEventError):
        check_event_registered("totally.unknown", debug=True, registry=reg)


def test_registry_processor_passthrough():
    reg = MapEventRegistry(
        [EventSpec(name="chat.turn_start", fields={"preview": FieldType("str")})]
    )
    reset_registry_for_tests(reg)
    out = registry_processor(None, "info", {"event": "chat.turn_start", "preview": "hi"})
    assert out["event"] == "chat.turn_start"


def test_registry_processor_warns_unregistered_in_debug(monkeypatch: pytest.MonkeyPatch):
    # The processor chain is the ONLY validation path (no separate emit API):
    # unknown names must warn in dev yet still pass the event dict through.
    from agentcore.config import settings

    monkeypatch.setattr(settings, "debug", True)
    monkeypatch.delenv("LOG_EVENT_REGISTRY_STRICT", raising=False)
    reset_registry_for_tests(MapEventRegistry([EventSpec(name="chat.turn_start")]))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        out = registry_processor(None, "info", {"event": "totally.unknown", "k": 1})
    assert out == {"event": "totally.unknown", "k": 1}  # never blocks the emit
    assert any(issubclass(w.category, UnregisteredLogEventWarning) for w in caught)


def test_duplicate_registration_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        MapEventRegistry(
            [EventSpec(name="chat.turn_start"), EventSpec(name="chat.turn_start")]
        )


def _load_sync_log_event_registry():
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "scripts" / "sync_log_event_registry.py"
    spec = importlib.util.spec_from_file_location("sync_log_event_registry", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_catalog_check_does_not_rewrite():
    mod = _load_sync_log_event_registry()
    before = mod.OUT.read_bytes()
    mtime = mod.OUT.stat().st_mtime_ns
    rc = mod.main(["--check"])
    assert isinstance(rc, int)
    assert mod.OUT.read_bytes() == before
    assert mod.OUT.stat().st_mtime_ns == mtime


def test_catalog_check_flags_missing_emit_name(monkeypatch, capsys):
    mod = _load_sync_log_event_registry()
    real_scan = mod.scan_events
    probe = "__catalog_check_probe__.missing"

    monkeypatch.setattr(mod, "scan_events", lambda: real_scan() | {probe})
    rc = mod.main(["--check"])
    captured = capsys.readouterr().out
    assert rc == 1
    assert probe in captured
    assert "never rewrites catalog.py" in captured
    assert probe not in mod.catalog_names_from_text(mod.OUT.read_text(encoding="utf-8"))


def test_catalog_emit_names_match_registry():
    """Emit-site names ∪ HISTORICAL_COMPAT must equal catalog names (order/text may drift)."""
    mod = _load_sync_log_event_registry()
    scanned = mod.scan_events()
    events, dead = mod.planned_catalog(scanned)
    actual = set(mod.catalog_names_from_text(mod.OUT.read_text(encoding="utf-8")))
    assert set(events) == actual
    assert dead == []
