"""Test output parsers for pytest / vitest / jest."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TestFailure:
    test_name: str
    file_path: str | None
    line: int | None
    message: str
    snippet: str | None


@dataclass
class TestRunResult:
    framework: str
    passed: int
    failed: int
    errors: int
    skipped: int
    duration_seconds: float | None
    failures: list[TestFailure] = field(default_factory=list)
    raw_output: str = ""


_RAW_OUTPUT_LIMIT = 4000
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mK]")
_VITEST_ASSERT = re.compile(
    r"AssertionError:\s*(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_VITEST_EXPECTED = re.compile(
    r"Expected[:\s]+(.+?)(?:\n|$)",
    re.IGNORECASE,
)
_VITEST_RECEIVED = re.compile(
    r"Received[:\s]+(.+?)(?:\n|$)",
    re.IGNORECASE,
)


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)

_PYTEST_SUMMARY = re.compile(
    r"(\d+)\s+passed(?:.*?(\d+)\s+failed)?(?:.*?(\d+)\s+error)?",
    re.IGNORECASE,
)
_PYTEST_DURATION = re.compile(r"in\s+([\d.]+)s")
_PYTEST_FAILED = re.compile(
    r"FAILED\s+(.+?)::(.+?)\s+-\s+(.+)",
)
_PYTEST_ERROR_LINE = re.compile(r"^E\s+(.+)", re.MULTILINE)

_VITEST_SUMMARY = re.compile(
    r"Tests\s+(\d+)\s+passed\s*\|\s*(\d+)\s+failed",
    re.IGNORECASE,
)
_VITEST_FAIL = re.compile(r"FAIL\s+(.+?)\s+>\s+(.+)", re.MULTILINE)

_JEST_SUMMARY = re.compile(
    r"Tests:\s+(\d+)\s+passed,\s*(\d+)\s+failed",
    re.IGNORECASE,
)
_JEST_FAIL = re.compile(r"●\s+(.+)", re.MULTILINE)


def _truncate_raw(text: str) -> str:
    if len(text) <= _RAW_OUTPUT_LIMIT:
        return text
    return text[:_RAW_OUTPUT_LIMIT] + "\n... [truncated]"


def _parse_pytest_failures(combined: str) -> list[TestFailure]:
    failures: list[TestFailure] = []
    for match in _PYTEST_FAILED.finditer(combined):
        file_path, test_name, message = match.groups()
        line_no: int | None = None
        file_display = file_path
        line_match = re.search(r":(\d+)", file_path)
        if line_match:
            line_no = int(line_match.group(1))
            file_display = file_path.rsplit(":", 1)[0]
        snippet = None
        tail = combined[match.end() : match.end() + 500]
        err_match = _PYTEST_ERROR_LINE.search(tail)
        if err_match:
            snippet = err_match.group(1).strip()
        failures.append(
            TestFailure(
                test_name=test_name.strip(),
                file_path=file_display.strip(),
                line=line_no,
                message=message.strip(),
                snippet=snippet,
            )
        )
    return failures


def parse_pytest_output(stdout: str, stderr: str) -> TestRunResult:
    """Parse pytest output."""
    combined = f"{stdout}\n{stderr}"
    passed = failed = errors = skipped = 0
    duration: float | None = None

    for line in combined.splitlines():
        lower = line.lower()
        if "passed" in lower or "failed" in lower or "error" in lower:
            summary = _PYTEST_SUMMARY.search(line)
            if summary:
                passed = int(summary.group(1) or 0)
                failed = int(summary.group(2) or 0)
                errors = int(summary.group(3) or 0)
        if "skipped" in lower:
            skip_match = re.search(r"(\d+)\s+skipped", line, re.IGNORECASE)
            if skip_match:
                skipped = int(skip_match.group(1))
        dur = _PYTEST_DURATION.search(line)
        if dur:
            duration = float(dur.group(1))

    failures = _parse_pytest_failures(combined)
    if not passed and not failed and not errors and failures:
        failed = len(failures)

    return TestRunResult(
        framework="pytest",
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration_seconds=duration,
        failures=failures,
        raw_output=_truncate_raw(combined),
    )


def parse_vitest_output(stdout: str, stderr: str) -> TestRunResult:
    """Parse vitest output."""
    combined = _strip_ansi(f"{stdout}\n{stderr}")
    passed = failed = skipped = 0
    duration: float | None = None

    summary = _VITEST_SUMMARY.search(combined)
    if summary:
        passed = int(summary.group(1))
        failed = int(summary.group(2))

    dur_match = re.search(r"Duration\s+([\d.]+)s", combined, re.IGNORECASE)
    if dur_match:
        duration = float(dur_match.group(1))

    failures: list[TestFailure] = []
    for match in _VITEST_FAIL.finditer(combined):
        file_path, test_name = match.groups()
        tail = combined[match.end() : match.end() + 800]
        message = ""
        assert_hit = _VITEST_ASSERT.search(tail)
        if assert_hit:
            message = assert_hit.group(1).strip()
        expected = _VITEST_EXPECTED.search(tail)
        received = _VITEST_RECEIVED.search(tail)
        snippet = None
        if expected or received:
            bits = []
            if expected:
                bits.append(f"Expected {expected.group(1).strip()}")
            if received:
                bits.append(f"Received {received.group(1).strip()}")
            snippet = "; ".join(bits)
        failures.append(
            TestFailure(
                test_name=test_name.strip(),
                file_path=file_path.strip(),
                line=None,
                message=message,
                snippet=snippet,
            )
        )
    if not passed and not failed and failures:
        failed = len(failures)

    return TestRunResult(
        framework="vitest",
        passed=passed,
        failed=failed,
        errors=0,
        skipped=skipped,
        duration_seconds=duration,
        failures=failures,
        raw_output=_truncate_raw(combined),
    )


def parse_jest_output(stdout: str, stderr: str) -> TestRunResult:
    """Parse jest output."""
    combined = f"{stdout}\n{stderr}"
    passed = failed = skipped = 0
    duration: float | None = None

    summary = _JEST_SUMMARY.search(combined)
    if summary:
        passed = int(summary.group(1))
        failed = int(summary.group(2))

    time_match = re.search(r"Time:\s+([\d.]+)\s*s", combined, re.IGNORECASE)
    if time_match:
        duration = float(time_match.group(1))

    failures: list[TestFailure] = []
    for match in _JEST_FAIL.finditer(combined):
        test_name = match.group(1).strip()
        failures.append(
            TestFailure(
                test_name=test_name,
                file_path=None,
                line=None,
                message="",
                snippet=None,
            )
        )
    if not passed and not failed and failures:
        failed = len(failures)

    return TestRunResult(
        framework="jest",
        passed=passed,
        failed=failed,
        errors=0,
        skipped=skipped,
        duration_seconds=duration,
        failures=failures,
        raw_output=_truncate_raw(combined),
    )


def parse_generic_output(stdout: str, stderr: str, exit_code: int) -> TestRunResult:
    """Fallback when framework output is not recognized."""
    combined = f"{stdout}\n{stderr}".strip()
    passed = 1 if exit_code == 0 else 0
    failed = 0 if exit_code == 0 else 1
    return TestRunResult(
        framework="unknown",
        passed=passed,
        failed=failed,
        errors=0,
        skipped=0,
        duration_seconds=None,
        failures=[],
        raw_output=_truncate_raw(combined),
    )
