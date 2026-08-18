"""Direct coverage for declared-vs-landed acceptance (exact / dir / glob only)."""

from __future__ import annotations

from agentcore.runtime.runs.file_acceptance import (
    REASON_PATH_MISMATCH,
    apply_declared_path_acceptance,
    declaration_allows_landed,
    landed_matches_declared,
)


def test_landed_matches_declared_exact_only():
    path = "AgentCore/文档/reviews/前端刷新审计-对话页面.md"
    assert landed_matches_declared(path, path) is True
    assert landed_matches_declared(path, "前端刷新审计-对话页面.md") is False
    assert landed_matches_declared("前端刷新审计-对话页面.md", path) is False


def test_landed_matches_declared_dir_prefix_and_glob():
    landed = "AgentCore/文档/reviews/a.md"
    assert landed_matches_declared(landed, "AgentCore/文档/reviews/") is True
    assert landed_matches_declared("AgentCore/文档/reviews", "AgentCore/文档/reviews/") is True
    assert landed_matches_declared(landed, "AgentCore/文档/research/") is False
    assert landed_matches_declared(landed, "AgentCore/文档/reviews/*.md") is True
    assert landed_matches_declared(landed, "AgentCore/文档/reviews/*.txt") is False


def test_declaration_allows_landed_nonempty_artifacts_ignores_artifact_dir():
    """artifacts 非空：只对声明路径做精确/目录/通配，artifact_dir 不兜底。"""
    landed = "AgentCore/文档/reviews/前端刷新审计-对话页面.md"
    assert (
        declaration_allows_landed(
            landed,
            artifacts=["前端刷新审计-对话页面.md"],
            artifact_dir="AgentCore/文档/reviews",
        )
        is False
    )
    assert (
        declaration_allows_landed(
            landed,
            artifacts=[landed],
            artifact_dir="AgentCore/文档/research",
        )
        is True
    )


def test_declaration_allows_landed_empty_artifacts_uses_artifact_dir_prefix():
    landed = "AgentCore/文档/reviews/a.md"
    assert (
        declaration_allows_landed(landed, artifacts=[], artifact_dir="AgentCore/文档/reviews")
        is True
    )
    assert (
        declaration_allows_landed(landed, artifacts=[], artifact_dir="AgentCore/文档/research")
        is False
    )
    assert declaration_allows_landed(landed, artifacts=[], artifact_dir="") is True


def test_apply_declared_path_acceptance_rejects_mismatch_only():
    landed = "AgentCore/文档/reviews/a.md"
    accepted = {"path": landed, "status": "accepted"}
    rejected = apply_declared_path_acceptance(
        accepted,
        artifacts=["a.md"],
        artifact_dir="AgentCore/文档/reviews",
    )
    assert rejected["status"] == "rejected"
    assert rejected["reason"] == REASON_PATH_MISMATCH
    assert rejected["path"] == landed

    keep = apply_declared_path_acceptance(
        {"path": landed, "status": "accepted"},
        artifacts=[landed],
        artifact_dir="",
    )
    assert keep["status"] == "accepted"
    assert "reason" not in keep

    already = {"path": landed, "status": "rejected", "reason": "run_failed"}
    assert apply_declared_path_acceptance(already, artifacts=["other.md"]) is already

    unconstrained = apply_declared_path_acceptance(
        {"path": landed, "status": "accepted"},
        artifacts=[],
        artifact_dir="",
    )
    assert unconstrained["status"] == "accepted"
