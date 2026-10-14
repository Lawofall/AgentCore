"""Graph tool schema: the model-facing door is nodes."""

from tests.delegate.conftest import Provider, tool


def test_schema_omits_retired_completion_criteria():
    t = tool(Provider([]))
    props = t.schema.parameters["properties"]
    assert "playbook" not in props
    assert "playbook_args" not in props
    assert "completion_criteria" not in props
    assert "finalize" not in props
    assert "completion_criteria" not in t.schema.description


def test_ceo_schema_is_nodes_only():
    """填参面只有节点 id / goal / depends_on。"""
    t = tool(Provider([]))
    assert t.schema.name == "graph"
    assert set(t.schema.parameters["properties"]) == {"nodes", "conclusion"}
    node_props = t.schema.parameters["properties"]["nodes"]["items"]["properties"]
    assert set(node_props) == {"id", "goal", "depends_on"}
    assert "deliverable" not in node_props
    assert "artifacts" not in node_props


def test_nested_delegate_description_switches_at_depth():
    from agentcore.tools.builtin.graph.schema import (
        DELEGATE_DESCRIPTION,
        NESTED_DELEGATE_DESCRIPTION,
    )

    t = tool(Provider([]))
    assert t.schema.description == DELEGATE_DESCRIPTION
    t._depth = 1
    assert t.schema.description == NESTED_DELEGATE_DESCRIPTION
