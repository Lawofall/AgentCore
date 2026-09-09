"""daily_review card is retired — write path rejects, coerce falls back to decision."""

from typing import get_args

from agentcore.runtime.checkpoints import AskCheckpointIntent, coerce_ask_checkpoint_intent
from agentcore.tools.builtin.ask_user.card import CARD_KINDS, parse_card


def test_daily_review_card_is_absent():
    assert "daily_review" not in CARD_KINDS
    err = parse_card("daily_review")
    assert isinstance(err, str) and "未知 card" in err
    assert "daily_review" not in get_args(AskCheckpointIntent)
    assert coerce_ask_checkpoint_intent("daily_review") == "decision"
