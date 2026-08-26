"""Field-level consumer ratchet — unread payload leaf names."""

from __future__ import annotations

from agentcore.conformance.field_consumer_baseline import (
    FIELD_CONSUMER_BASELINE,
    duplicate_baseline_leaves,
)
from agentcore.conformance.field_consumer_gate import (
    _IDFIELD_REL,
    _repo_root,
    format_field_orphan_reports,
    idfield_seed_names,
    run_field_consumer_gate,
    tokens_from_source,
)


def test_tokens_recognize_destructure_jsonproperty_rename_and_skip_comments():
    assert "from_agent_id" in tokens_from_source("const { from_agent_id } = p;")
    assert "from_agent_id" in tokens_from_source(
        '[JsonProperty("from_agent_id")] public string FromAgentId;'
    )
    assert "from_agent_id" in tokens_from_source(
        "const copy = { from_agent_id: src.from_agent_id };"
    )
    assert "audience" in tokens_from_source(
        "store.payload = event.payload;\nlater(store.payload.audience);"
    )
    assert "from_agent_id" not in tokens_from_source(
        "// from_agent_id\nconst x = 1;\n"
    )
    assert "from_agent_id" not in tokens_from_source(
        "/* from_agent_id */\nconst x = 1;\n"
    )
    assert "http" in tokens_from_source('const u = "http://example.com";\n')


def test_idfield_values_are_seeded_from_wire_table():
    root = _repo_root()
    src = (root / _IDFIELD_REL).read_text(encoding="utf-8")
    seeded = idfield_seed_names(src)
    assert "approval_id" in seeded
    assert "checkpoint_id" in seeded
    assert "ask_id" not in seeded


def test_baseline_groups_have_reasons_and_no_duplicate_leaves():
    assert duplicate_baseline_leaves() == {}
    for group in FIELD_CONSUMER_BASELINE:
        assert group.id
        assert len(group.reason) >= 12
        assert group.leaves


def test_field_consumer_gate_clean_tree():
    result = run_field_consumer_gate()
    assert result.errors == []
    assert result.new_orphans == [], format_field_orphan_reports(result)
    assert result.ok
    assert result.coverage is not None
    assert result.coverage.events == 85
    assert result.coverage.top_level_slots == 451
    assert result.coverage.scan_files > 0
    assert (result.coverage.repo_root.replace("\\", "/")).endswith("AgentCore") or (
        "AgentCore" in result.coverage.repo_root
    )


def test_gate_flags_unseen_leaf(monkeypatch):
    import agentcore.conformance.field_consumer_gate as gate

    real_walk = gate.walk_field_slots

    def extra_walk(payload_map, fields, extends):
        slots = real_walk(payload_map, fields, extends)
        slots.append(
            gate.FieldSlot(event="message_start", path="gate_probe_unused", depth=0)
        )
        return slots

    monkeypatch.setattr(gate, "walk_field_slots", extra_walk)
    result = gate.run_field_consumer_gate()
    assert not result.ok
    assert "gate_probe_unused" in result.new_orphans
    joined = "\n".join(gate.format_field_orphan_reports(result))
    assert "message_start.gate_probe_unused" in joined
    assert "Do not delete the contract field" in joined


def test_gate_does_not_flag_consumed_leaf_name_on_a_new_path(monkeypatch):
    """Leaf-name criterion: a new slot named like an already-read leaf stays green."""
    import agentcore.conformance.field_consumer_gate as gate

    real_walk = gate.walk_field_slots

    def extra_walk(payload_map, fields, extends):
        slots = real_walk(payload_map, fields, extends)
        slots.append(gate.FieldSlot(event="message_start", path="run_id", depth=0))
        return slots

    monkeypatch.setattr(gate, "walk_field_slots", extra_walk)
    result = gate.run_field_consumer_gate()
    assert result.errors == []
    assert "run_id" not in result.new_orphans
    assert result.ok


def test_release_gate_wires_field_consumer_cli():
    root = _repo_root()
    src = (root / "scripts" / "release-gate.mjs").read_text(encoding="utf-8")
    assert "check_event_field_consumers.py" in src
    listed = (root / "apps" / "server" / "scripts" / "README.md").read_text(
        encoding="utf-8"
    )
    assert "check_event_field_consumers.py" in listed
