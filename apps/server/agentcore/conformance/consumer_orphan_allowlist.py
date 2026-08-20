"""Allowlists for the consumer-orphan gate.

Keys: ``sse:<wire>`` | ``interaction:<kind>`` | ``process_step:<kind>``.
Each value must state why the type is exempt. Default empty — no silent exemptions.

``CONSUMER_ORPHAN_ALLOWLIST``: still in the generated contract, no live Python producer.
``PRODUCER_ORPHAN_ALLOWLIST``: Python producer, no desktop live UI consumer.
"""

from __future__ import annotations

CONSUMER_ORPHAN_ALLOWLIST: dict[str, str] = {}

PRODUCER_ORPHAN_ALLOWLIST: dict[str, str] = {}
