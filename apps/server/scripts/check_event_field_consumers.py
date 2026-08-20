#!/usr/bin/env python3
"""CLI: field-level event payload consumer ratchet.

Fails when ``events.generated.ts`` grows a leaf field name that no production
consumer (desktop / mobile / admin / town / protocol-fold-kit) reads, and that
name is not in the grouped baseline.

Usage (from apps/server)::

    uv run python scripts/check_event_field_consumers.py

Wired into ``pnpm release:gate`` (backend section) and unit pytest.
Independent of ``check_event_consumer_orphans.py`` (event-name gate).
"""

from __future__ import annotations

import argparse
import sys
import time

from agentcore.conformance.field_consumer_gate import (
    format_coverage,
    format_field_orphan_reports,
    run_field_consumer_gate,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Fail when a generated event payload leaf name has zero production "
            "consumer hits and is not baseline-exempt."
        )
    )
    parser.parse_args(argv)

    started = time.perf_counter()
    result = run_field_consumer_gate()
    elapsed = time.perf_counter() - started
    timing = f" ({elapsed:.2f}s)"
    if result.errors and not result.new_orphans:
        for err in result.errors:
            print(f"✗ field-consumer gate internal error: {err}")
        for line in format_coverage(result):
            print(line)
        print(f"  elapsed{timing}")
        return 1

    if result.ok:
        print("✓ no new unread event payload leaf names" + timing)
        for line in format_coverage(result):
            print(line)
        return 0

    print("✗ field-consumer gate FAILED:")
    for err in result.errors:
        print(f"  {err}")
    for line in format_field_orphan_reports(result):
        print(line)
    print(
        "  Baseline (requires reason): "
        "agentcore/conformance/field_consumer_baseline.py"
    )
    for line in format_coverage(result):
        print(line)
    print(f"  elapsed{timing}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
