"""Streamed ``read_text_lines`` — file size is not an admission ticket."""

from pathlib import Path

import pytest

from agentcore.workspace.limits import WORKSPACE_READ_MAX_BYTES
from agentcore.workspace.protocol import WorkspaceIOError
from agentcore.workspace.text_window import read_text_lines, read_text_lines_from_bytes


def test_read_text_lines_windows_file_over_5mib(tmp_path: Path):
    line = "row-%05d token\n"
    repeats = (WORKSPACE_READ_MAX_BYTES // len(line.encode("utf-8"))) + 50
    path = tmp_path / "big.txt"
    path.write_text("".join(line % i for i in range(repeats)), encoding="utf-8")
    window = read_text_lines(path, offset=10, limit=5)
    assert window.lines == [f"row-{i:05d} token" for i in range(9, 14)]
    assert window.start_line == 10
    assert window.end_line == 14
    assert window.total_lines == repeats


def test_read_text_lines_from_bytes_matches_disk(tmp_path: Path):
    text = "a\nb\nc\n"
    got = read_text_lines_from_bytes(text.encode("utf-8"), offset=2, limit=1)
    assert got.lines == ["b"]
    assert got.total_lines == 3


def test_read_text_lines_rejects_nul(tmp_path: Path):
    path = tmp_path / "bin.txt"
    path.write_bytes(b"ok\n\x00nope\n")
    with pytest.raises(WorkspaceIOError, match="二进制"):
        read_text_lines(path, offset=1, limit=10)
