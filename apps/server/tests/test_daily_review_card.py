"""Daily review preflight + apply unit tests."""

from __future__ import annotations

from agentcore.standing_tasks.review_apply import option_to_review_proposal
from agentcore.standing_tasks.review_preflight import EMPTY_REVIEW_SUMMARY
from agentcore.standing_tasks.templates import daily_review_goal
from agentcore.tools.builtin.ask_user.card import (
    card_max_options,
    parse_card,
    validate_card_shape,
)


def test_parse_daily_review_card():
    assert parse_card("daily_review") == "daily_review"
    assert "daily_review" in str(parse_card("nope"))


def test_daily_review_card_shape_ok():
    err = validate_card_shape(
        "daily_review",
        questions=[
            {
                "kind": "choice",
                "multiple": True,
                "options": [
                    {
                        "label": "偏好：简短回复",
                        "review_kind": "preference",
                        "body": "用户喜欢短回复",
                    }
                ],
            }
        ],
    )
    assert err is None
    assert card_max_options("daily_review") == 20


def test_daily_review_card_requires_multiple():
    err = validate_card_shape(
        "daily_review",
        questions=[
            {
                "kind": "choice",
                "multiple": False,
                "options": [{"label": "x", "review_kind": "rule", "body": "y"}],
            }
        ],
    )
    assert err is not None
    assert "multiple=true" in err


def test_option_to_review_proposal():
    p = option_to_review_proposal(
        {
            "label": "记一条规则",
            "review_kind": "rule",
            "body": "提交前先跑测试",
        }
    )
    assert p is not None
    assert p.kind == "rule"
    assert "测试" in p.body

    bad = option_to_review_proposal({"label": "x", "review_kind": "topic", "body": "y"})
    assert bad is None  # topic needs slug


def test_goal_mentions_daily_review_card():
    g = daily_review_goal()
    assert 'card="daily_review"' in g
    assert EMPTY_REVIEW_SUMMARY.startswith("今日无新料")
