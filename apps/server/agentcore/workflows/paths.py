"""Public URL path helpers for workflow triggers (no heavy imports)."""


def webhook_path(webhook_id: str) -> str:
    """Relative public path; clients prepend the API origin."""
    return f"/v1/hooks/workflows/{webhook_id}"
