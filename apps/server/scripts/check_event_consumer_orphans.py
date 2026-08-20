#!/usr/bin/env python3
"""CLI: protocol orphan gate (contract↔producer and producer↔desktop live UI).

Direction A: generated-contract types with no Python producer.
Direction B: Python producers with no desktop live UI consumer (sse dispatch /
handlers, client-tool fulfill, browser live, handoff). Fold/parity are not
scanned on direction B — they are exhaustive compile-time switches.

Usage (from apps/server)::

    uv run python scripts/check_event_consumer_orphans.py

Wired into ``pnpm release:gate`` (backend section) and unit pytest.
"""

from __future__ import annotations

import argparse
import sys
import time

from agentcore.conformance.consumer_orphan_gate import (
    format_coverage,
    format_orphan_reports,
    run_consumer_orphan_gate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when SSE / interaction / process-step types are in the generated "
            "contract with no Python producer, or have a Python producer with no "
            "desktop live UI consumer."
        )
    )
    parser.parse_args(argv)

    started = time.perf_counter()
    result = run_consumer_orphan_gate()
    elapsed = time.perf_counter() - started
    timing = f" ({elapsed:.2f}s)"
    if result.errors:
        for err in result.errors:
            print(f"✗ consumer-orphan gate internal error: {err}")
        for line in format_coverage(result):
            print(line)
        print(f"  elapsed{timing}")
        return 1

    if result.ok:
        print(
            "✓ no protocol orphans "
            "(contract without producer / producer without desktop live UI)"
            + timing
        )
        for line in format_coverage(result):
            print(line)
        return 0

    print("✗ consumer-orphan gate FAILED:")
    for line in format_orphan_reports(result):
        print(line)
    print(
        "  Whitelist (requires reason): "
        "agentcore/conformance/consumer_orphan_allowlist.py"
    )
    for line in format_coverage(result):
        print(line)
    print(f"  elapsed{timing}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
