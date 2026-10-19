"""Card-secret codes for paid seats. Plaintext is returned once at issue time."""

from __future__ import annotations

import hashlib
import secrets

from agentcore.billing.seats import SKUS, PaidPlan

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_BODY_GROUPS = 3
_GROUP_LEN = 4


def normalize_code(raw: str) -> str:
    """Uppercase, strip spaces, keep hyphens. Empty after strip → ''."""
    compact = "".join(raw.split())
    return compact.upper()


def hash_code(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("ascii")).hexdigest()


def prefix_of(normalized: str) -> str:
    """SKU head + first body group, for the admin roster (not enough to redeem)."""
    parts = normalized.split("-")
    if len(parts) >= 2:
        return f"{parts[0]}-{parts[1]}"
    return normalized[:8]


def generate_code(sku: PaidPlan) -> str:
    head = SKUS[sku].code_prefix
    groups = []
    for _ in range(_BODY_GROUPS):
        groups.append("".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LEN)))
    return f"{head}-{'-'.join(groups)}"


