"""Fingerprint a stored secret for settings views.

Plaintext never belongs in API responses; this is the only allowed residue
(prefix + suffix). Short values hide the prefix so the fingerprint cannot
reassemble most of the secret.
"""

_HEAD = 6
_TAIL = 4
_MIN_HIDDEN = 4
_FALLBACK = "••••"


def mask_secret(value: str) -> str:
    """Return ``sk-9e3...5f06``-style residue, or last-4 for short secrets."""
    text = value.strip()
    if len(text) <= _TAIL:
        return _FALLBACK
    if len(text) < _HEAD + _TAIL + _MIN_HIDDEN:
        return f"{_FALLBACK}{text[-_TAIL:]}"
    return f"{text[:_HEAD]}...{text[-_TAIL:]}"
