"""Stored-text codec: UTF-8 / BOM / GB18030 decode + write-back."""

from agentcore.workspace.file_kind import (
    decode_stored_text,
    decode_text_bytes,
    encode_stored_text,
)


def test_decode_utf8_and_bom_round_trip():
    assert decode_stored_text(b"hi") == ("hi", "utf-8")
    bom = "hi".encode("utf-8-sig")
    assert decode_stored_text(bom) == ("hi", "utf-8-sig")
    assert encode_stored_text("hi", "utf-8-sig").startswith(b"\xef\xbb\xbf")


def test_decode_gb18030_round_trip():
    raw = "你好世界".encode("gb18030")
    decoded = decode_stored_text(raw)
    assert decoded is not None
    text, encoding = decoded
    assert text == "你好世界"
    assert encoding == "gb18030"
    assert encode_stored_text(text, encoding) == raw


def test_nul_is_binary():
    assert decode_stored_text(b"ok\x00no") is None
    assert decode_text_bytes(b"ok\x00no") is None
