"""Always-on ``<设定>`` injection template (read-side equal-authority join)."""

# Unique owner for「题材偏好不得改路由」— the flat ``<设定>`` block formats this in.
_RULES_ROUTING_FENCE = (
    "硬约束：题材/领域偏好与历史任务不得改变本回合路由"
    "（直答/委派/调研/辩论以用户当前话为准）。"
)

# Single equal-authority block (Agent记忆与知识系统 · 取消权威档): no user-hard /
# AI-soft subsections. Body is already frontmatter/chrome-stripped at load time.
_RULES_TEMPLATE = """
<设定>
以下条目请一并遵循。
{routing_fence}

{body}
</设定>"""

# Back-compat aliases for older imports / demo sanitize mirrors (same fence text).
_MEMORY_ROUTING_FENCE = _RULES_ROUTING_FENCE


def _format_rules(rules_markdown: str | None) -> str | None:
    """Wrap always-on entries into one ``<设定>`` block, or None if empty."""
    if not rules_markdown or not rules_markdown.strip():
        return None
    body = rules_markdown.strip()
    if not body:
        return None
    return _RULES_TEMPLATE.format(body=body, routing_fence=_RULES_ROUTING_FENCE)
