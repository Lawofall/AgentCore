"""Unit tests for settings-view secret fingerprints."""

from agentcore.security.mask import mask_secret


def test_mask_secret_prefix_and_suffix():
    assert mask_secret("sk-9e3abcdefghijklmnopqrstuvwxyz5f06") == "sk-9e3...5f06"
    assert mask_secret("sk-openai-abcd1234") == "sk-ope...1234"
    assert mask_secret("sk-secret-1234") == "sk-sec...1234"
    assert mask_secret("sk-keep-me-4242") == "sk-kee...4242"
    assert mask_secret("sk-pool-secret-aaaa") == "sk-poo...aaaa"


def test_mask_secret_short_key_falls_back_to_last_four():
    assert mask_secret("sk-abcdef1234") == "••••1234"
    assert mask_secret("abc") == "••••"
    assert mask_secret("abcd") == "••••"
    assert mask_secret("  abcd  ") == "••••"
